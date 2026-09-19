import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
import unittest
from android_auth import AndroidDevices
import viewer


def proof():
    verifier = secrets.token_urlsafe(32)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


class AndroidAuthTests(unittest.TestCase):
    def test_one_time_binding_expiry_and_secret_storage(self):
        with tempfile.TemporaryDirectory() as folder:
            devices = AndroidDevices(folder)
            verifier, challenge = proof()
            code = devices.pair(challenge)
            with self.assertRaises(ValueError):
                devices.activate(code, "wrong device", proof()[0])
            result = devices.activate(code, "test phone", verifier)
            with self.assertRaises(ValueError):
                devices.activate(code, "replay", verifier)
            self.assertEqual(devices.authenticate(result["token"]), result["deviceId"])
            self.assertNotIn(result["token"].encode(), devices.file.read_bytes())
            self.assertNotIn(code.encode(), devices.file.read_bytes())
            expired = devices.pair(challenge)
            with devices.connect() as db:
                db.execute("UPDATE pairing SET expires=?", (time.time()-1,))
            with self.assertRaises(ValueError):
                devices.activate(expired, "late phone", verifier)

    def test_racing_activation_accepts_exactly_once(self):
        with tempfile.TemporaryDirectory() as folder:
            devices = AndroidDevices(folder)
            verifier, challenge = proof()
            code = devices.pair(challenge)
            def activate(_):
                try:
                    devices.activate(code, "phone", verifier)
                    return True
                except ValueError:
                    return False
            with ThreadPoolExecutor(max_workers=2) as pool:
                self.assertEqual(sum(pool.map(activate, range(2))), 1)

    def test_web_authorization_and_device_session_revocation(self):
        with tempfile.TemporaryDirectory() as folder:
            server = viewer.make_server("127.0.0.1", 0, Path(folder), "test-password-long-enough")
            threading.Thread(target=server.serve_forever, daemon=True).start()
            client = http.client.HTTPConnection("127.0.0.1", server.server_port)
            base = {"Content-Type":"application/json", "Origin":f"http://127.0.0.1:{server.server_port}"}
            def req(method, path, body=None, headers=None):
                client.request(method, path, json.dumps(body) if body is not None else None, headers or {})
                response = client.getresponse()
                return response.status, json.loads(response.read()), dict(response.getheaders())
            try:
                verifier, challenge = proof()
                self.assertEqual(req("POST", "/api/android/pair", {"challenge":challenge}, base)[0], 401)
                _, _, headers = req("POST", "/login", {"password":"test-password-long-enough"}, base)
                browser = {**base, "Cookie":headers["Set-Cookie"].split(";")[0]}
                self.assertEqual(req("POST", "/api/android/pair", {"challenge":challenge}, browser)[0], 403)
                _, listing, _ = req("GET", "/api/android/devices", headers=browser)
                browser["X-Viewer-CSRF"] = listing["csrfToken"]
                self.assertEqual(req("POST", "/api/android/pair", {"challenge":challenge}, {**browser,"Origin":"https://other.example"})[0], 403)
                _, pairing, _ = req("POST", "/api/android/pair", {"challenge":challenge}, browser)
                native = {"Content-Type":"application/json"}
                _, activation, _ = req("POST", "/api/android/activate", {"code":pairing["code"],"verifier":verifier,"name":"My Android"}, native)
                native["Authorization"] = "Bearer " + activation["token"]
                status, _, headers = req("POST", "/api/android/session", {}, native)
                self.assertEqual(status, 200)
                device_cookie = {"Cookie":headers["Set-Cookie"].split(";")[0]}
                self.assertEqual(req("GET", "/api/threads", headers=device_cookie)[0], 200)
                # A migration freezes all state changes but keeps the existing
                # device able to unlock and read the update instructions.
                marker = Path(folder) / "site.json"
                marker.write_text(json.dumps({"migrationTarget":"https://new.example/"}))
                self.assertEqual(req("GET", "/health")[1]["migrationTarget"], "https://new.example/")
                for path in ("/api/android/pair", "/api/android/activate", "/api/android/revoke", "/api/threads/new", "/api/threads/11111111-1111-1111-1111-111111111111/messages"):
                    self.assertEqual(req("POST", path, {}, browser)[0], 409)
                self.assertEqual(req("POST", "/api/android/session", {}, native)[0], 200)
                self.assertEqual(req("GET", "/api/threads", headers=device_cookie)[0], 200)
                self.assertFalse(req("GET", "/api/threads", headers=device_cookie)[1]["bridge"]["connected"])
                marker.unlink()
                self.assertEqual(req("POST", "/api/android/revoke", {"deviceId":activation["deviceId"]}, browser)[0], 200)
                self.assertEqual(req("GET", "/api/threads", headers=device_cookie)[0], 401)
                self.assertEqual(req("POST", "/api/android/session", {}, native)[0], 400)
                self.assertEqual(req("GET", "/api/threads", headers=browser)[0], 200)
            finally:
                client.close(); server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
