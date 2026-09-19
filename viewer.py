"""Small Codex conversation mirror with authenticated desktop messaging."""
import argparse
import collections
import datetime as dt
import gzip
import hashlib
import hmac
import http.cookies
import http.server
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import ssl
import subprocess
import sys
import signal
import tempfile
import threading
import time
from urllib.parse import urlsplit
from control import DesktopBridge, MessageQueue
from android_auth import AndroidDevices
from chatgpt_mirror import ChatGPTMirror
from images import ImageStore, extract_images

ROOT = Path(__file__).resolve().parent
ID_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
ID_IN_NAME = re.compile(r"([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})\.jsonl$")
TAIL_BYTES = 16 * 1024 * 1024
MAX_MESSAGES = 300
MAX_TEXT = 32000
MAX_THREAD_CHARS = 250000


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def atomic_write(file, value):
    file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=file.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(value)
        os.replace(temp, file)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def clean_user(text):
    # Desktop inserts these context envelopes as user-role messages.
    for tag in ("recommended_plugins", "environment_context", "permissions instructions",
                "app-context", "skills_instructions", "turn_aborted"):
        text = re.sub(r"<" + re.escape(tag) + r">[\s\S]*?</" + re.escape(tag) + r">", "", text)
    if text.lstrip().startswith(("# AGENTS.md instructions", "<collaboration_mode>", "<subagent_notification>")):
        return ""
    if "<send_user_message_question_reply>" in text:
        try:
            payload = text.split("<send_user_message_question_reply>", 1)[1].split("</send_user_message_question_reply>")[0]
            text = "\n\n".join(f"{row['question']}\n{row['answer']}" for row in json.loads(payload))
        except (ValueError, KeyError, TypeError):
            return ""
    return text.strip()


class Transcript:
    def __init__(self, file):
        self.file = file
        self.id = ID_IN_NAME.search(file.name).group(1)
        self.offset = 0
        self.pending = b""
        self.messages = []
        self.meta = {}
        self.truncated = False
        self.signature = None
        self.status = "unknown"
        self.has_responses = False
        self.fallback = []
        self.updated_at = ""

    def read(self, stat):
        signature = (stat.st_size, stat.st_mtime_ns)
        if signature == self.signature:
            return
        if stat.st_size < self.offset:
            self.__init__(self.file)
        with self.file.open("rb") as stream:
            if self.signature is None:
                first = stream.readline(2 * 1024 * 1024)
                try:
                    row = json.loads(first)
                    if row.get("type") == "session_meta":
                        self.meta = row.get("payload", {})
                except (ValueError, UnicodeError):
                    pass
            start = max(self.offset, stat.st_size - TAIL_BYTES)
            if start > self.offset:
                self.truncated = True
                self.pending = b""
                stream.seek(start)
                stream.readline()  # Discard a partial first line, not valid JSON.
            else:
                stream.seek(start)
            data = self.pending + stream.read(max(0, stat.st_size - stream.tell()))
            self.offset = stream.tell()
        lines = data.split(b"\n")
        self.pending = lines.pop()
        if len(self.pending) > TAIL_BYTES:
            self.pending = b""
            self.truncated = True
        for line in lines:
            try:
                row = json.loads(line)
                self.consume(row)
            except (ValueError, UnicodeError, TypeError, AttributeError):
                continue
        for messages in (self.messages, self.fallback):
            chars = sum(len(m["text"]) for m in messages)
            while len(messages) > MAX_MESSAGES or (chars > MAX_THREAD_CHARS and len(messages) > 1):
                chars -= len(messages.pop(0)["text"])
                self.truncated = True
        self.signature = signature

    def consume(self, row):
        if row.get("timestamp"):
            self.updated_at = row["timestamp"]
        p = row.get("payload", {})
        kind = row.get("type")
        if kind == "event_msg":
            event = p.get("type")
            if event == "task_started":
                self.status = "running"
            elif event in ("task_complete", "task_completed", "turn_completed"):
                self.status = "idle"
            elif event in ("turn_aborted", "turn_failed"):
                self.status = "stopped"
            if event in ("user_message", "agent_message"):
                role = "user" if event == "user_message" else "assistant"
                text = p.get("message", "")
                if isinstance(text, str):
                    text = clean_user(text) if role == "user" else text.strip()
                    if text:
                        self.fallback.append({"role": role, "phase": "", "text": text[:MAX_TEXT], "time": row.get("timestamp", "")})
            return
        if kind != "response_item" or p.get("type") != "message":
            return
        role = p.get("role")
        phase = p.get("phase") or p.get("channel") or ""
        if role not in ("user", "assistant") or phase == "analysis":
            return
        self.has_responses = True
        parts = []
        for item in p.get("content", []):
            if item.get("type") in ("input_text", "output_text"):
                parts.append(item.get("text", ""))
            elif item.get("type") in ("input_image", "image", "input_audio", "input_file"):
                parts.append("[附件：请在电脑上的 Codex 查看]")
        text = "\n".join(parts)
        if role == "user":
            text = clean_user(text)
        if not text.strip():
            return
        if len(text) > MAX_TEXT:
            text = text[:MAX_TEXT] + "\n[本条消息过长，完整内容请在电脑查看]"
        text, images = extract_images(text) if role == "user" else (text, [])
        self.messages.append({"role": role, "phase": phase, "text": text.strip(), "time": row.get("timestamp", ""), **({"images":images} if images else {})})
        if role == "assistant" and phase == "final":
            self.status = "idle"

    def export(self, title, stat):
        source = self.meta.get("source")
        if isinstance(source, dict) and "subagent" in source or source == "subagent":
            return None
        messages = self.messages if self.has_responses else self.fallback
        if not messages:
            return None
        first_user = next((m["text"] for m in messages if m["role"] == "user"), "未命名对话")
        return {"id": self.id, "kind":"codex", "title": title or first_user[:70],
                "workspace": self.meta.get("cwd", ""), "updatedAt": self.updated_at or dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat(),
                "status": self.status, "truncated": self.truncated, "messages": messages}


class Exporter:
    def __init__(self, codex_home, limit=30):
        self.home = codex_home
        self.limit = limit
        self.cache = {}

    def snapshot(self):
        titles = {}
        try:
            with (self.home / "session_index.jsonl").open("rb") as source:
                source.seek(max(0, source.seek(0, 2) - 2 * 1024 * 1024))
                for line in source:
                    try:
                        row = json.loads(line)
                        titles[row["id"]] = row.get("thread_name", "")
                    except (ValueError, KeyError, UnicodeError):
                        pass
        except FileNotFoundError:
            pass
        files = []
        for file in (self.home / "sessions").rglob("*.jsonl"):
            if ID_IN_NAME.search(file.name):
                try:
                    files.append((file, file.stat()))
                except FileNotFoundError:
                    continue
        files.sort(key=lambda pair: pair[1].st_mtime, reverse=True)
        threads = []
        active = set()
        for file, stat in files:
            active.add(file)
            transcript = self.cache.setdefault(file, Transcript(file))
            try:
                transcript.read(stat)
            except (OSError, PermissionError):
                continue
            thread = transcript.export(titles.get(transcript.id), stat)
            if thread:
                threads.append(thread)
            if len(threads) >= self.limit:
                break
        self.cache = {file: item for file, item in self.cache.items() if file in active}
        return sorted(threads, key=lambda t: t["updatedAt"], reverse=True)


def ingest(data_dir, batch):
    order = batch.get("order", [])
    if len(order) > 100 or any(not ID_RE.fullmatch(item) for item in order):
        raise ValueError("Invalid thread IDs")
    # A single atomic snapshot keeps index and messages consistent for readers.
    file = data_dir / "snapshot.json"
    try:
        previous = json.loads(file.read_bytes())
    except FileNotFoundError:
        previous = {"threads": []}
    by_id = {row["id"]: row for row in previous["threads"]}
    for row in batch.get("threads", []):
        if not ID_RE.fullmatch(row["id"]):
            raise ValueError("Invalid thread ID")
        by_id[row["id"]] = row
    snapshot = {"syncedAt": batch["syncedAt"], "bridge": batch.get("bridge", {"connected": False}), "pollInterval": batch.get("pollInterval", 5), "threads": [by_id[i] for i in order if i in by_id]}
    atomic_write(file, encoded(snapshot))


class SyncCadence:
    """Keep a sliding activity window; heartbeats and old history do not renew it."""
    def __init__(self, idle_interval=5, active_interval=2, active_window=180, clock=time.monotonic):
        self.idle_interval = idle_interval
        self.active_interval = active_interval
        self.active_window = active_window
        self.clock = clock
        self.active_until = 0
        self.message_hashes = None

    def activate(self):
        self.active_until = self.clock() + self.active_window

    def observe(self, threads):
        visible = [t for t in threads if t.get("kind") != "chatgpt" or t.get("loaded")]
        hashes = {t["id"]: hashlib.sha256(encoded(t.get("messages", []))).hexdigest() for t in visible}
        if self.message_hashes is not None and any(self.message_hashes.get(t["id"]) != hashes[t["id"]]
                and (t.get("kind") != "chatgpt" or t["id"] in self.message_hashes) for t in visible):
            self.activate()
        self.message_hashes = hashes

    def interval(self):
        return self.active_interval if self.clock() < self.active_until else self.idle_interval


def sync(args):
    exporter = Exporter(Path(args.codex_home).expanduser(), args.limit)
    sent = {}
    last_heartbeat = 0
    bridge = DesktopBridge(ROOT, args.data_dir) if args.ssh_host and args.control else None
    chats = ChatGPTMirror(ROOT, args.data_dir) if bridge else None
    cadence = SyncCadence(idle_interval=args.interval)
    previous_interval = None
    published_interval = None
    stopped = threading.Event()
    stop_file = Path(args.data_dir) / "stop.request"
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        while not stopped.is_set() and not stop_file.exists():
            try:
                threads = exporter.snapshot()
                local_ids = [t["id"] for t in threads]
                chat_threads, chat_connected = chats.snapshot() if chats else ([], False)
                threads += chat_threads
                cadence.observe(threads)
                hashes = {t["id"]: hashlib.sha256(encoded(t)).hexdigest() for t in threads}
                changed = [t for t in threads if sent.get(t["id"]) != hashes[t["id"]]]
                if bridge or changed or hashes != sent or cadence.interval() != published_interval or time.monotonic() - last_heartbeat > 30 or args.once:
                    batch = {"threads": changed, "order": [t["id"] for t in threads], "syncedAt": now(), "pollInterval": cadence.interval()}
                    if bridge:
                        batch["bridge"] = {**bridge.status(local_ids),"chatgptConnected":chat_connected,"chatgptThreadIds":[t["id"] for t in chat_threads]}
                        batch["receipts"] = list(bridge.receipts.values())
                    if args.ssh_host:
                        command = ["ssh", "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=10", "-o", "ServerAliveCountMax=2"]
                        if args.hostname:
                            command.extend(["-o", "HostName=" + args.hostname])
                        mode = "exchange" if bridge else "ingest"
                        command.extend([args.ssh_host, f"{shlex.quote(args.remote_python)} ~/.local/share/codex-viewer/viewer.py {mode} --gzip"])
                        result = subprocess.run(command, input=gzip.compress(encoded(batch)), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=45,
                                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                        if result.returncode:
                            raise RuntimeError(result.stderr.decode("utf-8", "replace").strip())
                        if bridge:
                            response = json.loads(result.stdout)
                            jobs = response.get("jobs", [])
                            if jobs:
                                cadence.activate()
                            bridge.deliver(jobs, atomic_write)
                            chats.request(response.get("chatRequests", []), cadence.interval())
                    else:
                        ingest(Path(args.data_dir), batch)
                    sent = hashes
                    published_interval = batch["pollInterval"]
                    last_heartbeat = time.monotonic()
                    if changed or args.once:
                        logging.info("synced %d changed / %d conversations", len(changed), len(threads))
            except Exception as error:
                logging.error("sync failed: %s", error)
                if args.once:
                    raise
            if args.once:
                return
            interval = cadence.interval()
            if interval != previous_interval:
                logging.info("sync cadence: %s seconds (%s)", interval, "active" if interval < args.interval else "idle")
                previous_interval = interval
            for _ in range(max(1, int(interval * 5))):
                if stopped.wait(0.2) or stop_file.exists():
                    break
    finally:
        if bridge:
            bridge.close()
        if chats:
            chats.close()



def make_server(host, port, data_dir, password, tls=False):
    session_key = secrets.token_bytes(32)
    attempts = collections.defaultdict(collections.deque)
    messages = MessageQueue(data_dir)
    devices = AndroidDevices(data_dir)
    images = ImageStore(data_dir / "uploads")
    chat_views = {}
    chat_views_lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def log_message(self, *_):
            pass  # Do not log credentials or conversation URLs.

        def reply(self, status, value, mime="application/json; charset=utf-8", cookie=None, filename=None):
            body = value if isinstance(value, bytes) else encoded(value)
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            if cookie:
                self.send_header("Set-Cookie", cookie)
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def authorized(self):
            try:
                cookie = http.cookies.SimpleCookie(self.headers.get("Cookie", ""))
                value = cookie["viewer_session"].value
                payload, signature = value.rsplit(".", 1)
                parts = payload.split(".")
                expected = hmac.new(session_key, payload.encode(), hashlib.sha256).hexdigest()
                return (1 <= len(parts) <= 2 and int(parts[0]) > time.time() and hmac.compare_digest(signature, expected)
                        and (len(parts) == 1 or devices.active(parts[1])))
            except (ValueError, KeyError, http.cookies.CookieError):
                return False

        def session_cookie(self, device_id=None):
            payload = str(int(time.time()) + 30 * 86400)
            if device_id:
                payload += "." + device_id
            signature = hmac.new(session_key, payload.encode(), hashlib.sha256).hexdigest()
            return f"viewer_session={payload}.{signature}; Path=/; HttpOnly; SameSite=Strict; Max-Age=2592000" + ("; Secure" if tls else "")

        def csrf(self):
            value = self.headers.get("Cookie", "")
            return hmac.new(session_key, ("csrf:" + value).encode(), hashlib.sha256).hexdigest()

        def snapshot(self):
            try:
                return json.loads((data_dir / "snapshot.json").read_bytes())
            except FileNotFoundError:
                return {"syncedAt": None, "threads": [], "bridge": {"connected": False}}

        def migration_target(self):
            try:
                target = json.loads((data_dir / "site.json").read_bytes()).get("migrationTarget", "")
                return target if isinstance(target, str) and target.startswith("https://") else ""
            except (OSError, ValueError, AttributeError):
                return ""

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/":
                return self.reply(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/health":
                return self.reply(200, {"ok": True, "migrationTarget": self.migration_target()})
            if path == "/android":
                return self.reply(200, (ROOT / "android.html").read_bytes(), "text/html; charset=utf-8")
            if path == "/downloads/codex-suixing.apk":
                apk = ROOT / "downloads" / "codex-suixing.apk"
                if apk.is_file():
                    return self.reply(200, apk.read_bytes(), "application/vnd.android.package-archive", filename="codex-suixing.apk")
                return self.reply(404, {"error": "安装包尚未发布"})
            if path in ("/vendor/markdown-it.min.js", "/app.js", "/android.js", "/polling.js"):
                return self.reply(200, (ROOT / path.lstrip("/")).read_bytes(), "application/javascript; charset=utf-8")
            if not self.authorized():
                return self.reply(401, {"error": "请先输入访问密码"})
            if path.startswith("/api/images/"):
                try:
                    content, mime = images.read(path.removeprefix("/api/images/"))
                    return self.reply(200, content, mime)
                except (OSError, ValueError):
                    return self.reply(404, {"error":"图片不存在"})
            if path == "/api/android/devices":
                return self.reply(200, {"devices": devices.list(), "csrfToken": self.csrf()})
            snapshot = self.snapshot()
            if self.migration_target():
                snapshot["bridge"] = {"connected": False}
            if path == "/api/threads":
                return self.reply(200, {"syncedAt": snapshot["syncedAt"], "bridge": snapshot.get("bridge", {}), "pollInterval": snapshot.get("pollInterval", 5), "csrfToken": self.csrf(), "creations": messages.for_thread("new"), "threads": [{k: v for k, v in t.items() if k != "messages"} for t in snapshot["threads"]]})
            match = re.fullmatch(r"/api/threads/([0-9a-f-]+)", path)
            if match and ID_RE.fullmatch(match[1]):
                row = next((t for t in snapshot["threads"] if t["id"] == match[1]), None)
                if row:
                    if row.get("kind")=="chatgpt":
                        with chat_views_lock:
                            chat_views[row["id"]]=time.time()
                            current={key:stamp for key,stamp in chat_views.items() if time.time()-stamp<60}
                            atomic_write(data_dir / "chat-views.json", encoded(current))
                    return self.reply(200, {**row, "deliveries": messages.for_thread(row["id"])})
            self.reply(404, {"error": "未找到内容"})

        def do_POST(self):
            path = urlsplit(self.path).path
            match = re.fullmatch(r"/api/threads/([0-9a-f-]+)/messages", path)
            create = path == "/api/threads/new"
            android = path in ("/api/android/pair", "/api/android/activate", "/api/android/session", "/api/android/revoke")
            if path != "/login" and not match and not create and not android:
                return self.reply(405, {"error": "不支持该操作"})
            # JSON body and strict same-origin prevent cross-site login forms.
            origin = self.headers.get("Origin")
            if origin and urlsplit(origin).netloc != self.headers.get("Host"):
                return self.reply(403, {"error": "无效来源"})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.reply(400, {"error": "需要 JSON"})
            if self.migration_target() and path not in ("/login", "/api/android/session"):
                return self.reply(409, {"error": "服务已迁移，请更新 APK 或打开新入口后操作"})
            if android:
                if path.endswith(("/pair", "/revoke")):
                    if not self.authorized():
                        return self.reply(401, {"error": "请先在网页登录"})
                    if not origin or not hmac.compare_digest(self.headers.get("X-Viewer-CSRF", ""), self.csrf()):
                        return self.reply(403, {"error": "请刷新后重试"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 2048:
                        raise ValueError("请求无效")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("请求无效")
                    if path.endswith("/pair"):
                        return self.reply(200, {"code": devices.pair(body.get("challenge")), "expiresIn": 300})
                    if path.endswith("/revoke"):
                        device_id = body.get("deviceId", "")
                        if not isinstance(device_id, str) or not ID_RE.fullmatch(device_id):
                            raise ValueError("设备无效")
                        devices.revoke(device_id)
                        return self.reply(200, {"ok": True})
                    if path.endswith("/activate"):
                        return self.reply(200, devices.activate(body.get("code"), body.get("name"), body.get("verifier")))
                    token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                    device_id = devices.authenticate(token)
                    return self.reply(200, {"ok": True}, cookie=self.session_cookie(device_id))
                except (ValueError, TypeError, KeyError) as error:
                    return self.reply(400, {"error": str(error) or "请求无效"})
            if match or create:
                if not self.authorized():
                    return self.reply(401, {"error": "请先登录"})
                if not origin or not hmac.compare_digest(self.headers.get("X-Viewer-CSRF", ""), self.csrf()):
                    return self.reply(403, {"error": "请刷新网页后再发送"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 12 * 1024 * 1024:
                        raise ValueError("消息过长")
                    body = json.loads(self.rfile.read(length))
                    if not isinstance(body, dict):
                        raise ValueError("无效消息")
                    text = body.get("text")
                    image_rows = body.get("images", [])
                    request_id = body.get("requestId", "")
                    if not isinstance(text, str) or (not text.strip() and not image_rows) or len(text) > 8000 or not isinstance(request_id, str) or not ID_RE.fullmatch(request_id):
                        raise ValueError("请输入消息或图片，文字最多 8000 字")
                    text = text.strip() or "请查看附图。"
                    snapshot = self.snapshot()
                    bridge = snapshot.get("bridge", {})
                    age = time.time() - dt.datetime.fromisoformat(snapshot["syncedAt"].replace("Z", "+00:00")).timestamp()
                    if age > 60 or not bridge.get("connected"):
                        return self.reply(409, {"error": "电脑暂未连接，请打开电脑上的 Codex 和同步程序后再发"})
                    if create:
                        project_id = body.get("projectId", "")
                        title = body.get("title", "")
                        if not isinstance(project_id, str) or project_id and not any(p["id"] == project_id for p in bridge.get("projects", [])):
                            raise ValueError("请选择可用的本机项目")
                        if not isinstance(title, str) or len(title) > 100:
                            raise ValueError("标题不能超过 100 字")
                        refs = images.accept(image_rows) if image_rows else []
                        return self.reply(202, messages.enqueue(request_id, "new", text, "create", {"projectId":project_id,"title":title.strip(), **({"images":refs} if refs else {})}))
                    target=next((t for t in snapshot["threads"] if t["id"]==match[1]),None)
                    chat=bool(target and target.get("kind")=="chatgpt")
                    allowed=bridge.get("chatgptThreadIds" if chat else "threadIds", [])
                    if match[1] not in allowed or target is None or (chat and not bridge.get("chatgptConnected")):
                        return self.reply(409, {"error": "请先在电脑 Codex 中打开这个对话"})
                    if chat and image_rows:
                        raise ValueError("桌面 ChatGPT 目前只能从手机传入文字，图片请在电脑添加")
                    refs = images.accept(image_rows) if image_rows else []
                    return self.reply(202, messages.enqueue(request_id, match[1], text, args={"channel":"chatgpt" if chat else "codex", **({"images":refs} if refs else {})}))
                except (ValueError, TypeError, AttributeError, KeyError) as error:
                    return self.reply(400, {"error": str(error) or "无效消息"})
            address = self.client_address[0]
            if len(attempts) > 4096:
                attempts.clear()
            queue = attempts[address]
            moment = time.monotonic()
            while queue and queue[0] < moment - 60:
                queue.popleft()
            if len(queue) >= 10:
                return self.reply(429, {"error": "请稍后再试"})
            queue.append(moment)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length < 4096:
                    raise ValueError()
                supplied = json.loads(self.rfile.read(length)).get("password", "")
                ok = isinstance(supplied, str) and hmac.compare_digest(supplied.encode(), password.encode())
            except (ValueError, TypeError, AttributeError):
                ok = False
            if not ok:
                return self.reply(401, {"error": "密码不正确"})
            queue.clear()
            self.reply(200, {"ok": True}, cookie=self.session_cookie())

    class Server(http.server.ThreadingHTTPServer):
        address_family = socket.AF_INET6 if ":" in host else socket.AF_INET
        daemon_threads = True
        request_queue_size = 64
        handshake_timeout = 5

        def __init__(self, *args, **kwargs):
            self.tls_context = None
            self.workers = threading.BoundedSemaphore(16)
            super().__init__(*args, **kwargs)

        def process_request(self, request, client_address):
            if not self.workers.acquire(blocking=False):
                self.shutdown_request(request)
                return
            try:
                super().process_request(request, client_address)
            except BaseException:
                self.workers.release()
                raise

        def process_request_thread(self, request, client_address):
            try:
                # Browsers can preconnect without sending a TLS ClientHello.
                # Keep handshakes off the accept loop and bound their lifetime.
                if self.tls_context is not None:
                    request.settimeout(self.handshake_timeout)
                    request = self.tls_context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
                    request.do_handshake()
                super().process_request_thread(request, client_address)
            except OSError:
                self.shutdown_request(request)
            finally:
                self.workers.release()

        def server_bind(self):
            if self.address_family == socket.AF_INET6:
                self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            super().server_bind()

    return Server((host, port), Handler)


def configure_tls(server, certificate, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certificate, key)
    server.tls_context = context


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("init", "serve", "sync", "ingest", "exchange"))
    parser.add_argument("--data-dir", default=str(ROOT / ".state"))
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--cert")
    parser.add_argument("--key")
    parser.add_argument("--ssh-host")
    parser.add_argument("--hostname")
    parser.add_argument("--remote-python", default="python3", help="Python executable on the synchronization server")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--interval", type=float, default=5, help="Idle delay; message activity uses 2 seconds for 3 minutes")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--gzip", action="store_true")
    parser.add_argument("--log-file")
    parser.add_argument("--control", action="store_true", help="Deliver authenticated web messages to the local desktop")
    args = parser.parse_args()
    handlers = [RotatingFileHandler(args.log_file, maxBytes=1024 * 1024, backupCount=2, encoding="utf-8")] if args.log_file else [logging.StreamHandler()]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=handlers)
    data = Path(args.data_dir)
    if args.mode == "init":
        if not (data / "password.txt").exists():
            atomic_write(data / "password.txt", secrets.token_urlsafe(18).encode())
        print("Password stored in", data / "password.txt")
    elif args.mode in ("ingest", "exchange"):
        raw = sys.stdin.buffer.read(32 * 1024 * 1024 + 1)
        if len(raw) > 32 * 1024 * 1024:
            raise ValueError("Batch too large")
        batch = json.loads(gzip.decompress(raw) if args.gzip else raw)
        ingest(data, batch)
        if args.mode == "exchange":
            jobs = MessageQueue(data).exchange(batch.get("receipts", []), batch.get("bridge", {}).get("connected", False))
            for job in jobs:
                try:
                    job["imageData"] = ImageStore(data / "uploads").transport(job.get("args", {}).get("images", []))
                except (OSError, ValueError):
                    job["imageData"] = []  # Desktop fails this job before sending, never silently loses an attachment.
            try:
                views=json.loads((data/"chat-views.json").read_bytes())
                requested=[key for key,stamp in sorted(views.items(),key=lambda row:row[1],reverse=True) if ID_RE.fullmatch(key) and time.time()-stamp<60][:3]
            except (OSError,ValueError,TypeError):
                requested=[]
            sys.stdout.buffer.write(encoded({"jobs": jobs,"chatRequests":requested}))
    elif args.mode == "sync":
        if not 1 <= args.limit <= 100 or args.interval < 2:
            parser.error("limit must be 1..100; interval must be at least 2 seconds")
        if not args.once:
            data.mkdir(parents=True, exist_ok=True)
            lock = (data / "sync.lock").open("a+b")
            if lock.tell() == 0:
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                logging.info("A sync process is already running")
                return
            (data / "stop.request").unlink(missing_ok=True)
            atomic_write(data / "sync.pid", str(os.getpid()).encode())
        try:
            sync(args)
        finally:
            if not args.once:
                (data / "sync.pid").unlink(missing_ok=True)
                lock.close()
    else:
        password = (data / "password.txt").read_text().strip()
        if len(password) < 16:
            parser.error("Password must contain at least 16 characters")
        tls = bool(args.cert and args.key)
        if not tls and args.host not in ("127.0.0.1", "::1", "localhost"):
            parser.error("Non-loopback listeners require --cert and --key")
        server = make_server(args.host, args.port, data, password, tls)
        if tls:
            configure_tls(server, args.cert, args.key)
        print(f"Viewer listening on {args.host}:{args.port}", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
