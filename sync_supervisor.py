"""Restart the mirror after an abnormal exit; never own model engines."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent


def supervise(command, state, restart_delay=5, stable_after=60):
    state.mkdir(parents=True, exist_ok=True)
    stop = state / 'sync-stop.flag'
    failures = 0
    while not stop.exists():
        crash = state / 'sync-crash.log'
        if crash.exists() and crash.stat().st_size > 1024 * 1024:
            os.replace(crash, state / 'sync-crash.previous.log')
        started = time.monotonic()
        with crash.open('ab', buffering=0) as output:
            child = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=output, stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            logging.info('Mirror started: pid=%s', child.pid)
            try:
                while child.poll() is None and not stop.exists():
                    time.sleep(0.25)
            finally:
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=10)
        if stop.exists():
            logging.info('Mirror stopped by request')
            return
        if child.returncode == 0:
            # Duplicate-launch protection also returns zero; do not relaunch it.
            logging.info('Mirror exited normally; no restart')
            return
        failures = 0 if time.monotonic() - started >= stable_after else failures
        delay = min(60, restart_delay * (2 ** min(failures, 4)))
        failures += 1
        logging.error('Mirror exited: code=%s; retry in %ss; details in sync-crash.log', child.returncode, delay)
        deadline = time.monotonic() + delay
        while time.monotonic() < deadline and not stop.exists():
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def main():
    state = Path(os.environ.get('CODEX_SUIXING_STATE_DIR') or ROOT / '.state').resolve()
    state.mkdir(parents=True, exist_ok=True)
    lock = (state / 'supervisor.lock').open('a+b')
    if lock.tell() == 0:
        lock.write(b'0'); lock.flush()
    lock.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return
    (state / 'sync-stop.flag').unlink(missing_ok=True)
    (state / 'stop.request').unlink(missing_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
        handlers=[RotatingFileHandler(state / 'supervisor.log', maxBytes=1024*1024, backupCount=2, encoding='utf-8')])
    (state / 'supervisor.pid').write_text(str(os.getpid()), encoding='ascii')
    supervise([sys.executable, str(ROOT / 'viewer.py'), *sys.argv[1:]], state)


if __name__ == '__main__':
    main()
