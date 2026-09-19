"""Durable relay queue and a local-only Codex desktop adapter."""
import json
import os
from pathlib import Path
import queue
import shutil
import sqlite3
import subprocess
import threading
import time
from images import ImageStore
from contextlib import contextmanager


class MessageQueue:
    def __init__(self, data_dir):
        self.file = Path(data_dir) / "messages.sqlite3"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, text TEXT NOT NULL,
                status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                created REAL NOT NULL, updated REAL NOT NULL, claimed REAL NOT NULL DEFAULT 0)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(messages)")}
            for name, definition in (("kind", "TEXT NOT NULL DEFAULT 'message'"), ("args", "TEXT NOT NULL DEFAULT '{}'"), ("result_thread", "TEXT NOT NULL DEFAULT ''")):
                if name not in columns:
                    db.execute(f"ALTER TABLE messages ADD COLUMN {name} {definition}")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.file, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def public(row):
        return {"id": row["id"], "threadId": row["thread_id"], "text": row["text"],
                "status": row["status"], "detail": row["detail"], "created": row["created"],
                "kind": row["kind"], "args": json.loads(row["args"]), "resultThreadId": row["result_thread"]}

    def enqueue(self, request_id, thread_id, text, kind="message", args=None):
        arguments = json.dumps(args or {}, sort_keys=True, ensure_ascii=False)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM messages WHERE id=?", (request_id,)).fetchone()
            if existing:
                if existing["thread_id"] != thread_id or existing["text"] != text or existing["kind"] != kind or existing["args"] != arguments:
                    raise ValueError("请求编号已用于另一条消息")
                return self.public(existing)
            count = db.execute("SELECT count(*) FROM messages WHERE status IN ('queued','delivering')").fetchone()[0]
            if count >= 20:
                raise ValueError("待发送消息过多，请等电脑接收后再发")
            moment = time.time()
            db.execute("INSERT INTO messages (id,thread_id,text,status,created,updated,kind,args) VALUES (?,?,?,'queued',?,?,?,?)",
                       (request_id, thread_id, text, moment, moment, kind, arguments))
            return self.public(db.execute("SELECT * FROM messages WHERE id=?", (request_id,)).fetchone())

    def for_thread(self, thread_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM messages WHERE thread_id=? ORDER BY created DESC LIMIT 8", (thread_id,)).fetchall()
        return [self.public(row) for row in reversed(rows)]

    def exchange(self, receipts, ready):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for receipt in receipts:
                if receipt.get("status") in ("sent", "failed", "uncertain"):
                    db.execute("UPDATE messages SET status=?,detail=?,result_thread=?,updated=? WHERE id=? AND status IN ('queued','delivering')",
                               (receipt["status"], str(receipt.get("detail", ""))[:500], receipt.get("resultThreadId", ""), time.time(), receipt["id"]))
            # Keep recent delivery receipts without accumulating message copies forever.
            db.execute("DELETE FROM messages WHERE status IN ('sent','failed','uncertain') AND updated<?", (time.time()-7*86400,))
            if not ready:
                return []
            rows = db.execute("SELECT * FROM messages WHERE status='queued' OR (status='delivering' AND claimed<?) ORDER BY created LIMIT 4", (time.time()-90,)).fetchall()
            for row in rows:
                db.execute("UPDATE messages SET status='delivering',claimed=?,updated=? WHERE id=?", (time.time(), time.time(), row["id"]))
            return [self.public(row) for row in rows]


class DesktopBridge:
    def __init__(self, root, state_dir=None):
        self.root = Path(root)
        self.state_dir = Path(state_dir or os.environ.get("CODEX_SUIXING_STATE_DIR") or self.root / ".state").resolve()
        self.process = None
        self.responses = queue.Queue()
        self.last_status = 0
        self.known_thread_ids = []
        self.live = {"connected": False, "threadIds": []}
        self.receipts_file = self.state_dir / "delivery-receipts.json"
        try:
            self.receipts = json.loads(self.receipts_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.receipts = {}

    def _read(self, process, responses):
        try:
            for line in process.stdout:
                responses.put(json.loads(line))
        except Exception:
            pass
        responses.put({"connected": False, "status": "uncertain", "detail": "桌面连接中断，未自动重发"})

    def call(self, request):
        if self.process is None or self.process.poll() is not None:
            node = os.environ.get("CODEX_SUIXING_NODE") or shutil.which("node")
            if not node:
                raise RuntimeError("本机未找到 Node.js")
            self.responses = queue.Queue()
            self.process = subprocess.Popen([node, str(self.root / "desktop_bridge.mjs")],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                encoding="utf-8", text=True,
                env={**os.environ, "CODEX_SUIXING_STATE_DIR": str(self.state_dir)},
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            threading.Thread(target=self._read, args=(self.process, self.responses), daemon=True).start()
        self.process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        try:
            return self.responses.get(timeout=45)
        except queue.Empty:
            self.process.kill()
            self.process.wait(timeout=5)
            self.process = None
            raise RuntimeError("桌面连接超时，发送结果未知")

    def status(self, thread_ids=None):
        if thread_ids is not None and sorted(thread_ids) != self.known_thread_ids:
            self.known_thread_ids = sorted(thread_ids)
            self.last_status = 0
        if time.monotonic() - self.last_status > 15:
            try:
                self.live = self.call({"mode": "status", "threadIds": self.known_thread_ids})
            except Exception:
                self.live = {"connected": False, "threadIds": [], "error": "请在电脑上打开 Codex"}
            self.last_status = time.monotonic()
        return self.live

    def deliver(self, jobs, write):
        for job in jobs:
            key = job["id"]
            if key in self.receipts:
                continue
            # Write BEFORE calling the desktop. A crash after submission must not
            # cause a second model turn when the server redelivers the lease.
            self.receipts[key] = {"id": key, "status": "uncertain", "detail": "发送结果未确认，请先查看对话；没有自动重发。"}
            write(self.receipts_file, json.dumps(self.receipts, ensure_ascii=False).encode())
            self.last_status = 0
            try:
                arguments = dict(job.get("args", {}))
                images = arguments.pop("images", [])
                arguments.pop("imagePaths", None)
                if images:
                    if arguments.get("channel") == "chatgpt":
                        raise ValueError("桌面 ChatGPT 暂不支持图片传入")
                    store = ImageStore(self.state_dir / "incoming")
                    refs = store.accept(job.get("imageData", []))
                    if [r["id"] for r in refs] != [r["id"] for r in images]:
                        raise ValueError("图片未完整传到电脑，请重新发送")
                    arguments["imagePaths"] = [str((store.directory / row["id"]).resolve()) for row in refs]
            except (ValueError, OSError) as error:
                self.receipts[key] = {"id": key, "status": "failed", "detail": str(error)[:300]}
                write(self.receipts_file, json.dumps(self.receipts, ensure_ascii=False).encode())
                continue
            try:
                result = self.call({"mode": "create" if job.get("kind") == "create" else "send", "id": key, "threadId": job["threadId"], "text": job["text"], **arguments})
                if result.get("status") in ("sent", "failed", "uncertain"):
                    self.receipts[key] = {"id": key, "status": result["status"], "detail": result.get("detail", ""), "resultThreadId": result.get("resultThreadId", "")}
            except Exception:
                pass
            write(self.receipts_file, json.dumps(self.receipts, ensure_ascii=False).encode())

    def close(self):
        process = self.process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()
            self.process = None
