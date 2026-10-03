import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid
from native_cli import NativeSession, RPCError
from control import DesktopBridge

ID = '11111111-1111-1111-1111-111111111111'


class FakeRPC:
    def __init__(self, **kwargs):
        self.callbacks = kwargs
        self.calls = []
        self.closed = False
        self.requests = {}
        self.process = SimpleNamespace(pid=123, poll=lambda: 0 if self.closed else None)

    def call(self, method, params=None):
        params = params or {}
        self.calls.append((method, params))
        if method == 'model/list':
            return {'data': [{'model': 'gpt-5.5', 'supportedReasoningEfforts': [{'reasoningEffort': 'high'}]}]}
        if method == 'account/read':
            return {'account': {'type': 'chatgpt'}, 'requiresOpenaiAuth': False}
        if method in ('thread/resume', 'thread/start'):
            return {'thread': {'id': params.get('threadId', ID)}}
        if method == 'turn/start':
            return {'turn': {'id': 'native-turn-id'}}
        return {}

    def write(self, row):
        self.calls.append(('reply', row))

    def close(self):
        self.closed = True


class NativeTests(unittest.TestCase):
    def session(self, root, factory=FakeRPC):
        session = NativeSession(root, factory)
        session.status([ID])
        return session

    def test_native_text_image_model_and_client_id(self):
        with tempfile.TemporaryDirectory() as root:
            session = self.session(root)
            image = Path(root) / '.state/incoming' / ('a' * 64 + '.jpg')
            image.parent.mkdir(parents=True)
            image.write_bytes(b'fixture')
            key = str(uuid.uuid4())
            result = session.send({'mode': 'send', 'id': key, 'threadId': ID, 'text': 'original user text',
                                   'imagePaths': [str(image)], 'model': 'gpt-5.5', 'thinking': 'high'})
            self.assertEqual(result['status'], 'sent')
            method, args = session.engines[ID].calls[-1]
            self.assertEqual(method, 'turn/start')
            self.assertEqual(args['clientUserMessageId'], key)
            self.assertEqual(args['input'], [{'type': 'text', 'text': 'original user text'}, {'type': 'localImage', 'path': str(image.resolve())}])
            self.assertEqual(args['model'], 'gpt-5.5')
            self.assertEqual(args['effort'], 'high')
            self.assertNotIn('codex_suixing', json.dumps(args))

    def test_writer_conflict_does_not_submit_or_forward(self):
        created = []
        class Locked(FakeRPC):
            def __init__(self, **kwargs):
                super().__init__(**kwargs);created.append(self)
            def call(self, method, params=None):
                if method == 'thread/resume':
                    raise RPCError('thread already has an active writer')
                return super().call(method, params)
        with tempfile.TemporaryDirectory() as root:
            session = self.session(root, Locked)
            result = session.send({'mode': 'send', 'id': str(uuid.uuid4()), 'threadId': ID, 'text': 'do not forward'})
            self.assertEqual(result['status'], 'failed')
            self.assertIn('桌面', result['detail'])
            self.assertFalse(any(method == 'turn/start' for rpc in created for method, _ in rpc.calls))
            self.assertTrue(created[-1].closed)

    def test_creation_stays_native_and_keeps_default_permissions(self):
        with tempfile.TemporaryDirectory() as root:
            session = self.session(root)
            result = session.send({'mode': 'create', 'id': str(uuid.uuid4()), 'text': 'new task', 'title': 'test title'})
            self.assertEqual(result['resultThreadId'], ID)
            calls = session.engines[ID].calls
            self.assertEqual([m for m, _ in calls], ['thread/start', 'thread/name/set', 'turn/start'])
            self.assertNotIn('approvalPolicy', calls[0][1])
            self.assertNotIn('sandbox', calls[0][1])

    def test_approvals_and_answers_require_exact_live_request(self):
        with tempfile.TemporaryDirectory() as root:
            session = self.session(root)
            engine = session.new_engine()
            session.engines[ID] = engine
            session.request(engine, {'method': 'item/commandExecution/requestApproval', 'id': 7,
                                     'params': {'threadId': ID, 'command': 'fixture command'}})
            result = session.send({'mode': 'decision', 'id': str(uuid.uuid4()), 'threadId': ID, 'promptId': '7:123', 'decision': 'decline'})
            self.assertEqual(result['status'], 'sent')
            self.assertEqual(engine.calls[-1], ('reply', {'id': 7, 'result': {'decision': 'decline'}}))
            self.assertEqual(session.send({'mode': 'decision', 'id': str(uuid.uuid4()), 'threadId': ID, 'promptId': '7:123', 'decision': 'accept'})['status'], 'failed')
            session.request(engine, {'method': 'item/tool/requestUserInput', 'id': 8,
                                     'params': {'threadId': ID, 'questions': [{'id': 'color', 'question': 'Which color?'}]}})
            result = session.send({'mode': 'decision', 'id': str(uuid.uuid4()), 'threadId': ID, 'promptId': '8:123', 'answers': {'color': 'blue'}})
            self.assertEqual(result['status'], 'sent')
            self.assertEqual(engine.calls[-1][1]['result']['answers'], {'color': {'answers': ['blue']}})

    def test_control_dispatch_bypasses_desktop_for_codex(self):
        with tempfile.TemporaryDirectory() as root:
            bridge = DesktopBridge(root)
            calls = []
            bridge.native.call = lambda request: calls.append(request) or {'status': 'sent'}
            bridge.call({'mode': 'send', 'channel': 'codex', 'text': 'test'})
            bridge.call({'mode': 'create', 'text': 'test'})
            self.assertEqual([r['mode'] for r in calls], ['send', 'create'])
            self.assertIsNone(bridge.process)

    def test_custom_state_keeps_image_and_endpoint_out_of_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'source'
            state = Path(folder) / 'portable data'
            image = state / 'incoming' / ('b' * 64 + '.png')
            image.parent.mkdir(parents=True)
            image.write_bytes(b'fixture')
            session = NativeSession(root, FakeRPC, state_dir=state)
            session.status([ID])
            result = session.send({'mode': 'send', 'id': str(uuid.uuid4()), 'threadId': ID, 'text': 'image', 'imagePaths': [str(image)]})
            self.assertEqual(result['status'], 'sent')
            self.assertEqual(session.engines[ID].calls[-1][1]['input'][1]['path'], str(image.resolve()))
            bridge = DesktopBridge(root, state)
            self.assertEqual(bridge.native.endpoint, (state / 'native-endpoint.json').resolve())
            self.assertEqual(bridge.receipts_file, (state / 'delivery-receipts.json').resolve())
            self.assertFalse((root / '.state').exists())

    def test_live_assistant_progress_excludes_reasoning(self):
        with tempfile.TemporaryDirectory() as root:
            session = self.session(root)
            engine = session.new_engine()
            def event(method, **params):
                session.event(engine, {'method': method, 'params': {'threadId': ID, **params}})
            event('item/started', item={'id': 'private', 'type': 'reasoning', 'text': 'private reasoning'})
            event('item/started', item={'id': 'analysis', 'type': 'agentMessage', 'phase': 'analysis', 'text': 'private analysis'})
            event('item/started', item={'id': 'public', 'type': 'agentMessage', 'phase': 'commentary', 'text': ''})
            event('item/agentMessage/delta', itemId='public', delta='visible progress')
            self.assertEqual(list(session.live_messages[ID]), ['public'])
            self.assertEqual(session.live_messages[ID]['public']['text'], 'visible progress')
            event('item/completed', item={'id': 'public', 'type': 'agentMessage', 'phase': 'commentary', 'text': 'visible progress done'})
            self.assertEqual(session.live_messages[ID]['public']['text'], 'visible progress done')


if __name__ == '__main__':
    unittest.main()
