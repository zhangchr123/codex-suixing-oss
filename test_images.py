import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid

from control import DesktopBridge, MessageQueue
from images import ImageStore, extract_images
import viewer

PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII="
CODEX = "11111111-1111-1111-1111-111111111111"
CHAT = "22222222-2222-2222-2222-222222222222"


class ImageTests(unittest.TestCase):
    def test_store_validation_and_desktop_delivery(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = ImageStore(root / "uploads")
            refs = store.accept([{"data": PNG}])
            self.assertEqual(store.accept([{"data": PNG}]), refs)
            for row in ({"data":"@@"}, {"data":base64.b64encode(b"<svg/>").decode()}, {"id":"../x.png","data":PNG}, {"data":"A"*3000000}):
                with self.assertRaises(ValueError):
                    store.accept([row])
            with self.assertRaises(ValueError):
                store.read("../password.txt")
            with self.assertRaises(ValueError):
                store.accept([{"data":PNG}]*5)
            bridge = DesktopBridge(root)
            calls = []
            bridge.call = lambda request: calls.append(request) or {"status":"sent"}
            job = {"id":str(uuid.uuid4()),"threadId":CODEX,"text":"图片内容？","args":{"images":refs,"channel":"codex"},"imageData":store.transport(refs)}
            bridge.deliver([job], viewer.atomic_write)
            path = Path(calls[0]["imagePaths"][0])
            self.assertEqual(path.read_bytes(),base64.b64decode(PNG))
            self.assertTrue(path.is_relative_to((root / ".state/incoming").resolve()))
            bridge.deliver([job], viewer.atomic_write)
            self.assertEqual(len(calls),1)
            broken = {**job,"id":str(uuid.uuid4()),"imageData":[]}
            bridge.deliver([broken], viewer.atomic_write)
            self.assertEqual(bridge.receipts[broken["id"]]["status"],"failed")
            self.assertEqual(len(calls),1)
            text, ids = extract_images('问题\n\n<codex_suixing_images>\n"C:/private/'+refs[0]["id"]+'"\n</codex_suixing_images>')
            self.assertEqual(text,"问题")
            self.assertEqual(ids,[refs[0]["id"]])

    def test_authenticated_upload_and_chat_channel_routing(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            batch={"order":[CODEX,CHAT],"threads":[{"id":CODEX,"kind":"codex","messages":[]},{"id":CHAT,"kind":"chatgpt","messages":[],"loaded":False}],"syncedAt":viewer.now(),
                   "bridge":{"connected":True,"threadIds":[CODEX],"chatgptConnected":True,"chatgptThreadIds":[CHAT]}}
            viewer.ingest(root,batch)
            server=viewer.make_server("127.0.0.1",0,root,"test-password-long-enough")
            threading.Thread(target=server.serve_forever,daemon=True).start()
            client=http.client.HTTPConnection("127.0.0.1",server.server_port)
            def req(method,path,body=None,headers=None):
                client.request(method,path,json.dumps(body) if body is not None else None,headers or {})
                response=client.getresponse()
                return response.status,response.read(),dict(response.getheaders())
            try:
                base={"Content-Type":"application/json","Origin":f"http://127.0.0.1:{server.server_port}"}
                _,_,h=req("POST","/login",{"password":"test-password-long-enough"},base)
                auth={**base,"Cookie":h["Set-Cookie"].split(";")[0]}
                auth["X-Viewer-CSRF"]=json.loads(req("GET","/api/threads",headers=auth)[1])["csrfToken"]
                payload={"text":"","requestId":str(uuid.uuid4()),"images":[{"data":PNG}]}
                self.assertEqual(req("POST",f"/api/threads/{CODEX}/messages",payload,base)[0],401)
                status,raw,_=req("POST",f"/api/threads/{CODEX}/messages",payload,auth)
                self.assertEqual(status,202)
                ref=json.loads(raw)["args"]["images"][0]
                self.assertEqual(req("GET","/api/images/"+ref["id"])[0],401)
                status,content,h=req("GET","/api/images/"+ref["id"],headers=auth)
                self.assertEqual(status,200);self.assertEqual(content,base64.b64decode(PNG));self.assertEqual(h["Content-Type"],"image/png")
                self.assertEqual(req("POST",f"/api/threads/{CODEX}/messages",payload,auth)[0],202)
                self.assertEqual(len(MessageQueue(root).for_thread(CODEX)),1)
                self.assertEqual(req("POST",f"/api/threads/{CHAT}/messages",payload,auth)[0],400)
                chat={"text":"继续","requestId":str(uuid.uuid4())}
                self.assertEqual(req("POST",f"/api/threads/{CHAT}/messages",chat,auth)[0],202)
                self.assertEqual(MessageQueue(root).for_thread(CHAT)[0]["args"]["channel"],"chatgpt")
                self.assertEqual(req("GET",f"/api/threads/{CHAT}",headers=auth)[0],200)
                self.assertIn(CHAT,json.loads((root/"chat-views.json").read_bytes()))
                batch["bridge"]["chatgptConnected"]=False;viewer.ingest(root,batch)
                self.assertEqual(req("POST",f"/api/threads/{CHAT}/messages",{**chat,"requestId":str(uuid.uuid4())},auth)[0],409)
                self.assertEqual(req("GET","/api/images/../password.txt",headers=auth)[0],404)
            finally:
                client.close();server.shutdown();server.server_close()


if __name__ == "__main__":
    unittest.main()
