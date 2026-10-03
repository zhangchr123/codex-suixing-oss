"""Authenticated loopback client; the CLI owner survives mirror restarts."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from relay import read_frame, write_frame


class NativeClient:
    def __init__(self, root, state_dir=None):
        self.root = Path(root)
        self.state = Path(state_dir or os.environ.get('CODEX_SUIXING_STATE_DIR') or self.root / '.state').resolve()
        self.endpoint = self.state / 'native-endpoint.json'

    def _call(self, request):
        endpoint = json.loads(self.endpoint.read_bytes())
        with socket.create_connection(('127.0.0.1', endpoint['port']), timeout=3) as connection:
            connection.settimeout(45)
            with connection.makefile('rwb') as stream:
                write_frame(stream, json.dumps({'token': endpoint['token'], 'request': request}, ensure_ascii=False).encode())
                return json.loads(read_frame(stream))

    def call(self, request):
        # Startup is only retried before handing any message to the owner.
        try:
            return self._call(request)
        except (FileNotFoundError, ConnectionRefusedError):
            process = subprocess.Popen([sys.executable, str(self.root / 'native_host.py')],
                env={**os.environ, "CODEX_SUIXING_STATE_DIR": str(self.state)}, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0,
                start_new_session=os.name != 'nt')
            for _ in range(30):
                if process.poll() is not None:
                    raise RuntimeError('CLI 服务未能启动')
                time.sleep(0.1)
                try:
                    return self._call(request)
                except (FileNotFoundError, ConnectionRefusedError):
                    continue
            raise RuntimeError('CLI 服务启动超时')
