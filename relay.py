"""Persistent SSH exchange, using bounded length-prefixed frames over stdio."""
import gzip
import json
import os
import queue
import struct
import subprocess
import threading

MAX_FRAME = 32 * 1024 * 1024


def read_frame(stream):
    prefix = stream.read(4)
    if not prefix:
        return None
    if len(prefix) != 4:
        raise EOFError('Relay frame interrupted')
    size = struct.unpack('<I', prefix)[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError('Relay frame exceeds limit')
    chunks, remaining = [], size
    while remaining:
        data = stream.read(remaining)
        if not data:
            raise EOFError('Relay frame interrupted')
        chunks.append(data)
        remaining -= len(data)
    return b''.join(chunks)


def write_frame(stream, data):
    if not 0 < len(data) <= MAX_FRAME:
        raise ValueError('Relay frame exceeds limit')
    stream.write(struct.pack('<I', len(data)) + data)
    stream.flush()


class SSHRelay:
    def __init__(self, command):
        self.command, self.process = command, None
        self.responses = None

    def close(self):
        if self.process:
            self.process.kill()
            self.process.wait(timeout=5)
            for stream in (self.process.stdin, self.process.stdout):
                stream.close()
            self.process = None

    def _reader(self, process, responses):
        try:
            while (frame := read_frame(process.stdout)) is not None:
                responses.put(json.loads(gzip.decompress(frame)))
        except Exception:
            pass
        responses.put(None)

    def exchange(self, batch):
        if self.process is None or self.process.poll() is not None:
            self.close()
            self.responses = queue.Queue()
            self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            threading.Thread(target=self._reader, args=(self.process, self.responses), daemon=True).start()
        try:
            data = json.dumps(batch, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
            write_frame(self.process.stdin, gzip.compress(data))
            result = self.responses.get(timeout=30)
            if result is None:
                raise RuntimeError('同步连接中断，正在重新连接')
            return result
        except Exception:
            self.close()
            raise
