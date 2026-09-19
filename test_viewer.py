import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
import viewer
from control import MessageQueue, DesktopBridge

ID = "11111111-1111-1111-1111-111111111111"


class ViewerTests(unittest.TestCase):
    def test_activity_window_ignores_history_and_heartbeats(self):
        clock = [1000.0]
        cadence = viewer.SyncCadence(clock=lambda: clock[0])
        thread = {"id": ID, "messages": [{"text":"old message"}], "updatedAt":"first"}
        cadence.observe([thread])
        self.assertEqual(cadence.interval(), 5)
        clock[0] += 10
        cadence.observe([{**thread, "updatedAt":"heartbeat", "status":"idle"}])
        self.assertEqual(cadence.interval(), 5)
        thread["messages"].append({"text":"new reply"})
        cadence.observe([thread])
        self.assertEqual(cadence.interval(), 2)
        clock[0] += 179
        cadence.observe([thread])
        self.assertEqual(cadence.interval(), 2)
        clock[0] += 1
        self.assertEqual(cadence.interval(), 5)
        cadence.activate()  # A phone request also starts a fresh window.
        clock[0] += 120
        thread["messages"].append({"text":"another reply"})
        cadence.observe([thread])
        clock[0] += 179
        self.assertEqual(cadence.interval(), 2)
        clock[0] += 1
        self.assertEqual(cadence.interval(), 5)
        cadence.observe([])  # Pruning history is not an exchange.
        self.assertEqual(cadence.interval(), 5)
        chat = {"id":ID,"kind":"chatgpt","messages":[],"loaded":False}
        cadence.observe([chat])
        chat.update(loaded=True,messages=[{"text":"historical ChatGPT message"}])
        cadence.observe([chat])
        self.assertEqual(cadence.interval(),5)
        chat["messages"].append({"text":"new ChatGPT reply"})
        cadence.observe([chat])
        self.assertEqual(cadence.interval(),2)

    def test_queue_deduplication_and_crash_recovery(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            queue = MessageQueue(data)
            key = str(uuid.uuid4())
            first = queue.enqueue(key, ID, "继续任务")
            self.assertEqual(queue.enqueue(key, ID, "继续任务"), first)
            with self.assertRaises(ValueError):
                queue.enqueue(key, ID, "different text")
            jobs = queue.exchange([], True)
            self.assertEqual(len(jobs), 1)
            self.assertEqual(queue.exchange([], True), [])
            bridge = DesktopBridge(data)
            calls = []
            def uncertain_send(request):
                calls.append(request)
                raise RuntimeError("connection lost after submission")
            bridge.call = uncertain_send
            bridge.deliver(jobs, viewer.atomic_write)
            restarted = DesktopBridge(data)
            restarted.call = uncertain_send
            restarted.deliver(jobs, viewer.atomic_write)
            self.assertEqual(len(calls), 1)
            queue.exchange(list(restarted.receipts.values()), True)
            self.assertEqual(queue.for_thread(ID)[0]["status"], "uncertain")

    def test_message_auth_csrf_offline_and_create(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            snapshot = {"order":[ID],"threads":[{"id":ID,"title":"test","messages":[]}],"syncedAt":viewer.now(),
                        "bridge":{"connected":True,"threadIds":[ID],"projects":[{"id":ID,"label":"project"}]},"pollInterval":2}
            viewer.ingest(data,snapshot)
            server = viewer.make_server("127.0.0.1",0,data,"test-password-long-enough")
            threading.Thread(target=server.serve_forever,daemon=True).start()
            client = http.client.HTTPConnection("127.0.0.1",server.server_port)
            def req(method,path,body=None,headers=None):
                client.request(method,path,json.dumps(body) if body is not None else None,headers or {})
                r=client.getresponse();return r.status,json.loads(r.read()),dict(r.getheaders())
            try:
                base={"Content-Type":"application/json","Origin":f"http://127.0.0.1:{server.server_port}"}
                path=f"/api/threads/{ID}/messages"
                body={"text":"继续任务","requestId":str(uuid.uuid4())}
                self.assertEqual(req("POST",path,body,base)[0],401)
                _,_,h=req("POST","/login",{"password":"test-password-long-enough"},base)
                auth={**base,"Cookie":h["Set-Cookie"].split(";")[0]}
                self.assertEqual(req("POST",path,body,auth)[0],403)
                _,info,_=req("GET","/api/threads",headers=auth)
                self.assertEqual(info["pollInterval"],2)
                self.assertIn(b"class PollingCadence", (Path(__file__).parent/"polling.js").read_bytes())
                auth["X-Viewer-CSRF"]=info["csrfToken"]
                self.assertEqual(req("POST",path,body,{**auth,"Origin":"https://evil.example"})[0],403)
                self.assertEqual(req("POST",path,body,auth)[0],202)
                self.assertEqual(req("POST",path,body,auth)[0],202)
                self.assertEqual(len(MessageQueue(data).for_thread(ID)),1)
                new={"requestId":str(uuid.uuid4()),"text":"新建任务","title":"手机测试","projectId":ID}
                self.assertEqual(req("POST","/api/threads/new",new,auth)[0],202)
                self.assertEqual(MessageQueue(data).for_thread("new")[0]["kind"],"create")
                snapshot["bridge"]["connected"]=False
                snapshot["pollInterval"]=5
                viewer.ingest(data,snapshot)
                self.assertEqual(req("GET","/api/threads",headers=auth)[1]["pollInterval"],5)
                self.assertEqual(req("POST",path,{**body,"requestId":str(uuid.uuid4())},auth)[0],409)
            finally:
                client.close();server.shutdown();server.server_close()

    def test_incremental_privacy_and_partial_utf8(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / ("rollout-" + ID + ".jsonl")
            rows = [
                {"type": "session_meta", "payload": {"id": ID, "cwd": "C:/project"}},
                {"type": "response_item", "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "PRIVATE SYSTEM"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "<environment_context>PRIVATE ENV</environment_context>"}]}},
                {"type": "response_item", "payload": {"type": "function_call_output", "output": "SECRET TOOL OUTPUT"}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "channel": "analysis", "content": [{"type": "output_text", "text": "PRIVATE REASONING"}]}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant", "phase": "commentary", "content": [{"type": "output_text", "text": "正在工作"}]}},
            ]
            file.write_bytes(b"\n".join(viewer.encoded(r) for r in rows) + b"\n")
            item = viewer.Transcript(file)
            item.read(file.stat())
            self.assertEqual([m["text"] for m in item.messages], ["正在工作"])
            raw = viewer.encoded({"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "你好世界"}]}}) + b"\n"
            split = raw.index("好".encode()) + 1
            with file.open("ab") as stream:
                stream.write(raw[:split])
            item.read(file.stat())
            self.assertEqual(len(item.messages), 1)
            with file.open("ab") as stream:
                stream.write(raw[split:])
            item.read(file.stat())
            item.read(file.stat())
            self.assertEqual([m["text"] for m in item.messages], ["正在工作", "你好世界"])

    def test_atomic_delta_ingest_and_prune(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            thread = {"id": ID, "title": "测试", "messages": []}
            viewer.ingest(data, {"order": [ID], "threads": [thread], "syncedAt": "one"})
            viewer.ingest(data, {"order": [ID], "threads": [], "syncedAt": "two"})
            self.assertEqual(json.loads((data / "snapshot.json").read_bytes())["threads"], [thread])
            viewer.ingest(data, {"order": [], "threads": [], "syncedAt": "three"})
            self.assertEqual(json.loads((data / "snapshot.json").read_bytes())["threads"], [])
            with self.assertRaises(ValueError):
                viewer.ingest(data, {"order": ["../password.txt"], "threads": [], "syncedAt": "four"})

    def test_auth_read_only_and_xss_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            thread = {"id": ID, "title": "<script>alert(1)</script>", "messages": [{"role": "user", "text": "<img src=x onerror=alert(1)>"}]}
            viewer.ingest(data, {"order": [ID], "threads": [thread], "syncedAt": "now"})
            server = viewer.make_server("127.0.0.1", 0, data, "test-password-long-enough")
            runner = threading.Thread(target=server.serve_forever, daemon=True)
            runner.start()
            client = http.client.HTTPConnection("127.0.0.1", server.server_port)
            def req(method, path, body=None, headers=None):
                client.request(method, path, body, headers or {})
                response = client.getresponse()
                result = response.status, response.read(), dict(response.getheaders())
                return result
            try:
                self.assertEqual(req("GET", "/api/threads")[0], 401)
                self.assertEqual(req("GET", "/.state/password.txt")[0], 401)
                self.assertEqual(req("POST", "/login", '{"password":"wrong"}', {"Content-Type": "application/json"})[0], 401)
                status, _, headers = req("POST", "/login", '{"password":"test-password-long-enough"}', {"Content-Type": "application/json"})
                self.assertEqual(status, 200)
                self.assertIn("HttpOnly", headers["Set-Cookie"])
                cookie = {"Cookie": headers["Set-Cookie"].split(";")[0]}
                status, body, _ = req("GET", "/api/threads", headers=cookie)
                self.assertEqual(status, 200)
                self.assertNotIn("messages", json.loads(body)["threads"][0])
                self.assertEqual(req("GET", "/api/threads/" + ID, headers=cookie)[0], 200)
                self.assertEqual(req("GET", "/.state/password.txt", headers=cookie)[0], 404)
                self.assertEqual(req("POST", "/api/execute", headers=cookie)[0], 405)
                self.assertEqual(req("GET", "/api/threads", headers={"Cookie": "viewer_session=9999999999.forged"})[0], 401)
            finally:
                client.close()
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
