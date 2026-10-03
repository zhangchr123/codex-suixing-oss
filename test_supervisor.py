"""Real child-process regression: crash recovery and explicit stop."""
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from sync_supervisor import supervise


class SupervisorTests(unittest.TestCase):
    def test_crash_restarts_but_explicit_stop_does_not(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            script = state / 'child.py'
            script.write_text("from pathlib import Path\nimport sys,time\ns=Path(sys.argv[1]);n=s/'runs';i=int(n.read_text())+1 if n.exists() else 1;n.write_text(str(i))\nprint('startup evidence',flush=True)\nif i==1:raise RuntimeError('isolated crash')\nwhile True:time.sleep(.1)\n")
            worker = threading.Thread(target=supervise, args=([sys.executable, str(script), str(state)], state), kwargs={'restart_delay': 0.02})
            worker.start()
            try:
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    try:
                        if (state / 'runs').read_text() == '2':break
                    except OSError:pass
                    time.sleep(.05)
                self.assertEqual((state / 'runs').read_text(), '2')
                (state / 'sync-stop.flag').write_text('stop')
                worker.join(timeout=3)
                self.assertFalse(worker.is_alive())
                self.assertEqual((state / 'runs').read_text(), '2')
                self.assertIn('isolated crash', (state / 'sync-crash.log').read_text())
            finally:
                (state / 'sync-stop.flag').write_text('stop')
                worker.join(timeout=3)

    def test_normal_exit_does_not_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            supervise([sys.executable, '-c', 'print("normal exit")'], state, restart_delay=0.02)
            self.assertEqual((state / 'sync-crash.log').read_text().strip(), 'normal exit')


if __name__ == '__main__':
    unittest.main()
