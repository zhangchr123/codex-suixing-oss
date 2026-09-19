"""Cross-platform lifecycle and configuration tests. No real SSH or Codex calls."""
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import companion
from settings import ROOT, bridge_env, default_state, read_config, save_config, validate


class CompanionTests(unittest.TestCase):
    def test_private_config_and_no_shared_context(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "private data"
            config = save_config(state, {"sshHost": "my-relay", "url": "https://relay.example.com", "control": False})
            self.assertEqual(read_config(state)["url"], "https://relay.example.com/")
            self.assertEqual(bridge_env(state, config)["CODEX_SUIXING_CONTEXT_THREAD_ID"], "")
            if os.name != "nt":
                self.assertEqual((state / "connection.json").stat().st_mode & 0o777, 0o600)
            for values in [{"sshHost": "-oProxyCommand=bad"}, {"url": "https://user:pass@example.com"},
                           {"url": "http://example.com"}, {"control": True}, {"contextThreadId": "not-a-uuid"}]:
                with self.assertRaises(ValueError):
                    validate({**config, **values})

    def test_macos_paths_and_launch_agent_arguments(self):
        home = Path("/Users/example")
        self.assertEqual(default_state("darwin", home), home / "Library/Application Support/CodexSuixing")
        with tempfile.TemporaryDirectory(prefix="suixing space ") as folder:
            state = Path(folder)
            parsed = plistlib.loads(plistlib.dumps(companion.launch_agent(state)))
            self.assertEqual(parsed["ProgramArguments"][-1], str(state))
            self.assertTrue(parsed["RunAtLoad"])
            self.assertNotIn("KeepAlive", parsed)  # Stop must not immediately respawn.
            self.assertEqual(parsed["WorkingDirectory"], str(ROOT))

    def test_real_worker_lock_stop_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "state"
            codex = Path(folder) / "codex"
            (codex / "sessions").mkdir(parents=True)
            command = [sys.executable, str(ROOT / "viewer.py"), "sync", "--data-dir", str(state),
                       "--codex-home", str(codex), "--interval", "2"]
            # A stale PID is insufficient evidence that a worker is ours.
            state.mkdir()
            (state / "sync.pid").write_text(str(os.getpid()))
            self.assertFalse(companion.is_running(state))
            for _ in range(2):
                child = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 8
                    while not companion.is_running(state) and time.monotonic() < deadline:
                        if child.poll() is not None:
                            self.fail(child.stderr.read().decode())
                        time.sleep(0.05)
                    self.assertTrue(companion.is_running(state))
                    second = subprocess.run(command, capture_output=True, timeout=5)
                    self.assertEqual(second.returncode, 0)
                    self.assertIsNone(child.poll())
                    # Wait for one real local ingest before asking the worker to stop.
                    while not (state / "snapshot.json").exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue((state / "snapshot.json").exists())
                    companion.stop(state)
                    self.assertEqual(child.wait(timeout=5), 0)
                    self.assertFalse(companion.is_running(state))
                    self.assertFalse((state / "sync.pid").exists())
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.wait()
                    child.stderr.close()


if __name__ == "__main__":
    unittest.main()
