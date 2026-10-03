import base64
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from PIL import Image
from cloud_mirror import cloud_thread
from media import MediaMirror, MediaStore
from control import MessageQueue
import viewer

ID = '11111111-1111-1111-1111-111111111111'


class UpgradeTests(unittest.TestCase):
    def test_relocated_incoming_image_is_still_previewable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'new'
            incoming = root / '.state/incoming'
            incoming.mkdir(parents=True)
            old = Path(directory) / 'old/incoming'
            Image.new('RGB', (8, 8), (50, 90, 130)).save(incoming / 'image.png')
            (root / '.state/relocated-paths.json').write_text(json.dumps({str(old): str(incoming)}), encoding='utf-8')
            mirror = MediaMirror(root, Path(directory) / 'codex')
            self.assertIsNotNone(mirror.local_image(str(old / 'image.png'), ''))
            self.assertTrue(mirror.store.directory.is_relative_to(root.resolve()))

    def test_explicit_images_only_and_original_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workspace = root / 'work'
            workspace.mkdir()
            file = workspace / 'figure with spaces.png'
            Image.new('RGBA', (12, 8), (20, 80, 130, 100)).save(file)
            original = file.read_bytes()
            outside = root / 'private.png'
            Image.new('RGB', (2, 2)).save(outside)
            mirror = MediaMirror(root, root / 'codex')
            text = f'![图](<{file}>)\n![外部]({outside})\n![远程](https://example.com/image.png)'
            rows, needed = mirror.rewrite([{'workspace': str(workspace), 'messages': [{'text': text}]}])
            self.assertEqual(len(needed), 1)
            self.assertIn('/api/media/', rows[0]['messages'][0]['text'])
            self.assertIn(str(outside), rows[0]['messages'][0]['text'])
            self.assertEqual(file.read_bytes(), original)
            store = MediaStore(root / 'remote')
            payload = mirror.transport(needed, set())
            store.accept(payload)
            key = payload[0]['id']
            self.assertEqual(store.read(key)[1], 'image/png')
            with self.assertRaises(ValueError):
                store.read('../password.txt')
            with self.assertRaises(ValueError):
                store.accept([{'id': key, 'data': base64.b64encode(b'wrong').decode()}])
            self.assertEqual(mirror.transport(needed, needed), [])

    def test_markers_and_automatic_context_are_filtered(self):
        self.assertEqual(viewer.clean_user('<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>'), '')
        transcript = viewer.Transcript(Path('rollout-' + ID + '.jsonl'))
        transcript.consume({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '继续\n<codex_suixing_message_id>' + ID + '</codex_suixing_message_id>'}]}})
        self.assertEqual(transcript.messages[0]['text'], '继续')
        self.assertEqual(transcript.messages[0]['requestId'], ID)
        self.assertEqual(viewer.clean_user('<codex_internal_context source="goal"><objective>任务</objective></codex_internal_context>'), '')
        self.assertEqual(viewer.clean_user('<heartbeat><instructions>自动任务</instructions></heartbeat>'), '')
        self.assertEqual(viewer.clean_user('<in-app-browser-context source="ambient">界面</in-app-browser-context>\n## My request:\n还有多久'), '还有多久')
        self.assertEqual(viewer.clean_user('# Files mentioned by the user:\n## image.png: C:/Temp/image.png\n\n## My request:\n请看这里\n<image name="1">图片内容</image>'), '请看这里\n图片内容')
        self.assertEqual(viewer.clean_assistant('正文\n<oai-mem-citation><citation_entries>metadata</citation_entries></oai-mem-citation>'), '正文')
        self.assertEqual(viewer.clean_assistant('```html\n<oai-mem-citation>示例</oai-mem-citation>\n```'), '```html\n<oai-mem-citation>示例</oai-mem-citation>\n```')

    def test_native_image_attachment_previews(self):
        with tempfile.TemporaryDirectory() as directory:
            import io
            output = io.BytesIO()
            Image.new('RGB', (8, 8), 'blue').save(output, format='PNG')
            mirror = MediaMirror(directory, Path(directory) / 'codex')
            url = 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode()
            transcript = viewer.Transcript(Path('rollout-' + ID + '.jsonl'), mirror)
            transcript.consume({'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '<image name="1">'}, {'type': 'input_image', 'image_url': url}, {'type': 'input_text', 'text': '</image>\n请看看'}]}})
            messages = transcript.messages
            self.assertNotIn('<image', messages[0]['text'])
            self.assertNotIn('base64', messages[0]['text'])
            self.assertIn('/api/media/', messages[0]['text'])
            _, needed = mirror.rewrite([{'id': ID, 'messages': messages}])
            self.assertEqual(len(mirror.transport(needed, set())), 1)
            self.assertEqual(mirror.inline_image('https://example.com/private.png'), None)

    def test_server_etags_are_authenticated_and_invalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = {'order': [ID], 'threads': [{'id': ID, 'messages': [{'role': 'assistant', 'text': 'one'}]}], 'syncedAt': viewer.now()}
            viewer.ingest(root, batch)
            server = viewer.make_server('127.0.0.1', 0, root, 'test-password-long-enough')
            threading.Thread(target=server.serve_forever, daemon=True).start()
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            def request(method, path, body=None, headers=None):
                connection.request(method, path, body, headers or {})
                r = connection.getresponse()
                return r.status, r.read(), dict(r.getheaders())
            try:
                _, _, h = request('POST', '/login', json.dumps({'password': 'test-password-long-enough'}), {'Content-Type': 'application/json'})
                auth = {'Cookie': h['Set-Cookie'].split(';')[0]}
                path = '/api/threads/' + ID
                status, first, headers = request('GET', path, headers=auth)
                self.assertEqual(status, 200)
                etag = headers['ETag']
                self.assertEqual(request('GET', path, headers={**auth, 'If-None-Match': etag})[:2], (304, b''))
                self.assertEqual(request('GET', path, headers={'If-None-Match': etag})[0], 401)
                batch['threads'][0]['messages'][0]['text'] = 'two'
                viewer.ingest(root, batch)
                status, second, _ = request('GET', path, headers={**auth, 'If-None-Match': etag})
                self.assertEqual(status, 200)
                self.assertNotEqual(first, second)
                _, plain, _ = request('GET', '/app.js')
                status, compressed, headers = request('GET', '/app.js', headers={'Accept-Encoding': 'gzip'})
                import gzip
                self.assertEqual(status, 200)
                self.assertEqual(headers['Content-Encoding'], 'gzip')
                self.assertEqual(gzip.decompress(compressed), plain)
                self.assertLess(len(compressed), len(plain))
            finally:
                connection.close()
                server.shutdown()
                server.server_close()

    def test_creation_receipt_belongs_to_created_thread(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = MessageQueue(directory)
            key = str(uuid.uuid4())
            queue.enqueue(key, 'new', '开始任务', 'create')
            queue.exchange([{'id': key, 'status': 'sent', 'resultThreadId': ID}], False)
            self.assertEqual(queue.for_thread(ID)[0]['id'], key)

    def test_cloud_ids_do_not_claim_full_history(self):
        row = cloud_thread({'id': 'task_test', 'title': '云任务', 'status': 'running', 'url': 'https://evil.example/task'})
        self.assertTrue(viewer.ID_RE.fullmatch(row['id']))
        self.assertEqual(row['status'], 'running')
        self.assertEqual(row['cloudUrl'], '')
        self.assertTrue(row['metadataOnly'])
        self.assertEqual(row['messages'], [])
        self.assertEqual(row['id'], cloud_thread({'id': 'task_test'})['id'])


if __name__ == '__main__':
    unittest.main()
