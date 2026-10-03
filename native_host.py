"""Local persistent owner for CLI turns, independent of mirror restarts."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socketserver
import threading
import time
from relay import read_frame, write_frame
from native_cli import NativeSession

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('CODEX_SUIXING_STATE_DIR') or ROOT / '.state').resolve()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temp.chmod(0o600)
    os.replace(temp, path)


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    session = NativeSession(ROOT, state_dir=STATE)
    def publish_live():
        last = None
        while True:
            with session.state_lock:
                data = {tid: [dict(m) for m in messages.values() if m['text']] for tid, messages in session.live_messages.items()}
            signature = json.dumps(data, sort_keys=True, ensure_ascii=False)
            if signature != last:
                save(STATE / 'native-live.json', data)
                last = signature
            time.sleep(0.25)
    threading.Thread(target=publish_live, daemon=True).start()
    lock = threading.RLock()
    file = STATE / 'native-receipts.json'
    try:
        receipts = json.loads(file.read_bytes())
    except (OSError, ValueError):
        receipts = {}
    try:
        session.projects = json.loads((STATE / 'native-projects.json').read_bytes())
    except (OSError, ValueError):
        pass

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(50)
            try:
                data = json.loads(read_frame(self.rfile))
                if not isinstance(data.get('token'), str) or not hmac.compare_digest(data['token'], token):
                    return
                request = data['request']
                with lock:
                    if request.get('mode') == 'status':
                        if 'projects' in request:
                            session.projects = request['projects']
                            save(STATE / 'native-projects.json', session.projects)
                        result = session.status(request.get('threadIds'))
                        result['projects'] = [{'id': p['projectId'], 'label': p['label']} for p in session.projects]
                    else:
                        key = request.get('id')
                        fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
                        prior = receipts.get(key)
                        if prior:
                            result = prior['result'] if prior['fingerprint'] == fingerprint else {'status': 'failed', 'detail': '请求编号已用于另一条消息'}
                        else:
                            result = {'status': 'uncertain', 'detail': '结果未确认，请先查看对话；没有自动重发'}
                            receipts[key] = {'fingerprint': fingerprint, 'result': result}
                            save(file, receipts)
                            result = session.send(request)
                            receipts[key]['result'] = result
                            save(file, receipts)
                write_frame(self.wfile, json.dumps(result, ensure_ascii=False).encode('utf-8'))
            except Exception:
                try:
                    write_frame(self.wfile, b'{"status":"uncertain","detail":"CLI connection unavailable"}')
                except OSError:
                    pass

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True

    with Server(('127.0.0.1', 0), Handler) as server:
        save(STATE / 'native-endpoint.json', {'port': server.server_address[1], 'token': token, 'pid': os.getpid()})
        server.serve_forever()


if __name__ == '__main__':
    main()
