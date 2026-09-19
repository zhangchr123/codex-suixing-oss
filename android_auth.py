"""One-time phone pairing and revocable device tokens (no password in the APK)."""
from contextlib import contextmanager
import hashlib
import base64
from pathlib import Path
import re
import secrets
import sqlite3
import time
import uuid

TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")


class AndroidDevices:
    def __init__(self, folder):
        self.file = Path(folder) / "android.sqlite3"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS pairing (hash TEXT PRIMARY KEY, expires REAL NOT NULL, challenge TEXT NOT NULL)")
            db.execute("""CREATE TABLE IF NOT EXISTS devices (
                id TEXT PRIMARY KEY, hash TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL, last_used REAL NOT NULL)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.file, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(value):
        if not isinstance(value, str) or not TOKEN.fullmatch(value):
            raise ValueError("绑定凭据无效，请重新绑定")
        return hashlib.sha256(value.encode()).hexdigest()

    def pair(self, challenge):
        self.digest(challenge)
        code = secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute("DELETE FROM pairing WHERE expires<?", (time.time(),))
            if db.execute("SELECT count(*) FROM pairing").fetchone()[0] >= 20:
                raise ValueError("绑定请求过多，请五分钟后重试")
            db.execute("INSERT INTO pairing VALUES (?,?,?)", (self.digest(code), time.time()+300, challenge))
        return code

    def activate(self, code, name, verifier):
        digest = self.digest(code)
        self.digest(verifier)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("设备名称无效")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM pairing WHERE hash=? AND expires>?", (digest, time.time())).fetchone()
            if not row:
                raise ValueError("绑定码已使用或过期，请在网页重新绑定")
            if row["challenge"] != challenge:
                raise ValueError("请使用发起绑定的那台手机")
            db.execute("DELETE FROM pairing WHERE hash=?", (digest,))
            token, device_id, moment = secrets.token_urlsafe(32), str(uuid.uuid4()), time.time()
            db.execute("INSERT INTO devices VALUES (?,?,?,?,?,?)", (device_id, self.digest(token), name.strip(), moment, moment+365*86400, moment))
        return {"token": token, "deviceId": device_id}

    def authenticate(self, token):
        digest = self.digest(token)
        with self.connect() as db:
            row = db.execute("SELECT id FROM devices WHERE hash=? AND expires>?", (digest, time.time())).fetchone()
            if not row:
                raise ValueError("手机绑定已失效，请在网页重新绑定")
            db.execute("UPDATE devices SET last_used=? WHERE id=?", (time.time(), row["id"]))
        return row["id"]

    def active(self, device_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM devices WHERE id=? AND expires>?", (device_id, time.time())).fetchone() is not None

    def list(self):
        with self.connect() as db:
            rows = db.execute("SELECT id,name,created,expires,last_used FROM devices WHERE expires>? ORDER BY created DESC", (time.time(),)).fetchall()
        return [dict(row) for row in rows]

    def revoke(self, device_id):
        with self.connect() as db:
            db.execute("DELETE FROM devices WHERE id=?", (device_id,))
