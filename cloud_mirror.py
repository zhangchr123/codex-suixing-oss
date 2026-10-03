"""Read Codex Cloud task metadata using the locally authenticated official CLI."""
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import uuid
from urllib.parse import urlsplit


def cli_executable():
    found = shutil.which('codex.exe') or shutil.which('codex.cmd') or shutil.which('codex')
    if not found:
        raise RuntimeError('本机未找到 Codex CLI')
    if os.name == 'nt' and Path(found).suffix.lower() != '.exe':
        candidate = Path(found).parent / 'node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/vendor/x86_64-pc-windows-msvc/bin/codex.exe'
        if candidate.is_file():
            return str(candidate)
        raise RuntimeError('未找到 Codex CLI 本机执行文件')
    return found


def cloud_thread(task):
    task_id = task.get('id') or task.get('task_id')
    if not isinstance(task_id, str) or not task_id:
        return None
    stamp = task.get('updated_at') or task.get('created_at') or task.get('updatedAt')
    if isinstance(stamp, (int, float)):
        stamp = dt.datetime.fromtimestamp(stamp / 1000 if stamp > 1e11 else stamp, dt.timezone.utc).isoformat()
    if not isinstance(stamp, str):
        stamp = ''
    raw = task.get('status', 'unknown')
    if isinstance(raw, dict):
        raw = raw.get('type', 'unknown')
    state = str(raw).lower()
    status = 'running' if state in ('running', 'in_progress', 'pending', 'queued') else 'stopped' if state in ('failed', 'cancelled', 'canceled', 'error') else 'idle' if state in ('completed', 'complete', 'ready', 'done') else 'unknown'
    url = task.get('url', '')
    if not isinstance(url, str) or urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in ('chatgpt.com', 'chat.openai.com'):
        url = ''
    return {'id': str(uuid.uuid5(uuid.NAMESPACE_URL, 'codex-cloud:' + task_id)), 'kind': 'cloud',
            'title': task.get('title') or task.get('name') or task_id, 'workspace': 'Codex Cloud',
            'updatedAt': stamp, 'status': status, 'cloudStatus': str(raw), 'cloudTaskId': task_id,
            'cloudUrl': url, 'messages': [], 'metadataOnly': True}


class CloudMirror:
    def __init__(self, root, state_dir=None):
        self.cache = Path(state_dir or os.environ.get('CODEX_SUIXING_STATE_DIR') or Path(root) / '.state') / 'cloud-cache.json'
        self.lock = threading.Lock()
        self.threads = []
        self.state = {'connected': False, 'checkedAt': None, 'error': '正在检查云端任务'}
        try:
            self.threads = json.loads(self.cache.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        threading.Thread(target=self._run, daemon=True).start()

    def snapshot(self):
        with self.lock:
            return list(self.threads), dict(self.state)

    def _run(self):
        while True:
            try:
                result = subprocess.run([cli_executable(), 'cloud', 'list', '--json', '--limit', '20'],
                                        capture_output=True, timeout=25,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                if result.returncode:
                    # Auth failures can contain account details; do not copy raw stderr to the relay.
                    raise RuntimeError('云任务读取失败，请检查本机 CLI 登录与网络')
                data = json.loads(result.stdout)
                threads = [row for task in data.get('tasks', []) if (row := cloud_thread(task))]
                with self.lock:
                    self.threads = threads
                    self.state = {'connected': True, 'checkedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
                                  'error': '', 'taskCount': len(threads), 'metadataOnly': True}
                self.cache.parent.mkdir(parents=True, exist_ok=True)
                temp = self.cache.with_suffix('.tmp')
                temp.write_text(json.dumps(threads, ensure_ascii=False), encoding='utf-8')
                os.replace(temp, self.cache)
            except Exception:
                with self.lock:
                    self.state = {**self.state, 'connected': False, 'error': '云任务暂未连接；显示上次同步内容'}
            threading.Event().wait(30)
