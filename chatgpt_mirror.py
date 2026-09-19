"""Mirror the desktop app's existing ChatGPT chats through its local tools."""
import json
from pathlib import Path
import threading
import os
import tempfile
from control import DesktopBridge


class ChatGPTMirror:
    def __init__(self, root, state_dir=None):
        self.root = Path(root)
        self.client = DesktopBridge(root, state_dir)
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.requests = []
        self.delay = 5
        self.connected = False
        self.threads = []
        self.cache = self.client.state_dir / "chatgpt-cache.json"
        try:
            self.threads = json.loads(self.cache.read_bytes())
        except (OSError, ValueError):
            pass
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def close(self):
        self.stopped.set()
        self.wake.set()
        self.worker.join(timeout=46)

    def request(self, ids, delay):
        ids = sorted(set(ids))[:3]
        with self.lock:
            changed = ids != self.requests or delay != self.delay
            self.requests, self.delay = ids, delay
        if changed:
            self.wake.set()

    def snapshot(self):
        with self.lock:
            return list(self.threads), self.connected

    def _run(self):
        while not self.stopped.is_set():
            try:
                with self.lock:
                    requests = list(self.requests)
                result = self.client.call({"mode":"chat_snapshot", "threadIds":requests})
                with self.lock:
                    self.connected = bool(result.get("connected"))
                    if self.connected:
                        previous = {row["id"]: row for row in self.threads}
                        self.threads = [({**row, "messages":previous[row["id"]]["messages"], "loaded":True,
                                          "truncated":previous[row["id"]].get("truncated",False)}
                                         if not row.get("loaded") and previous.get(row["id"], {}).get("loaded") else row)
                                        for row in result["threads"]]
                if self.connected:
                    self.cache.parent.mkdir(parents=True, exist_ok=True)
                    fd, temp = tempfile.mkstemp(dir=self.cache.parent, prefix=".chat-")
                    try:
                        with os.fdopen(fd, "w", encoding="utf-8") as output:
                            json.dump(self.threads, output, ensure_ascii=False)
                        os.replace(temp, self.cache)
                    finally:
                        if os.path.exists(temp):
                            os.unlink(temp)
            except Exception:
                with self.lock:
                    self.connected = False
            with self.lock:
                delay = self.delay
            self.wake.wait(delay)
            self.wake.clear()
        self.client.close()
