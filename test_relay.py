import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import time
import unittest
from control import MessageQueue
from relay import SSHRelay, read_frame, write_frame, MAX_FRAME
import viewer

ID = '11111111-1111-1111-1111-111111111111'
REQUEST = '22222222-2222-2222-2222-222222222222'


class RelayTests(unittest.TestCase):
    def test_reused_transport_lease_receipts_and_reconnect(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            queue = MessageQueue(root)
            queue.enqueue(REQUEST, ID, 'isolated queued message')
            relay = SSHRelay([sys.executable, str(viewer.ROOT / 'viewer.py'), 'exchange-stream', '--data-dir', folder])
            batch = {'threads': [{'id': ID, 'messages': []}], 'order': [ID], 'syncedAt': viewer.now(), 'bridge': {'connected': True}}
            try:
                first = relay.exchange(batch)
                self.assertEqual(len(first['jobs']), 1)
                pid = relay.process.pid
                # Queue lease prevents a second handoff on the same connection.
                self.assertEqual(relay.exchange(batch)['jobs'], [])
                self.assertEqual(relay.process.pid, pid)
                (root / 'foreground.txt').write_text(str(time.time()))
                self.assertTrue(relay.exchange(batch)['foreground'])
                relay.close()
                batch['receipts'] = [{'id': REQUEST, 'status': 'sent'}]
                self.assertEqual(relay.exchange(batch)['jobs'], [])
                self.assertNotEqual(relay.process.pid, pid)
                self.assertEqual(queue.for_thread(ID)[0]['status'], 'sent')
            finally:
                relay.close()

    def test_partial_and_oversized_frames_rejected(self):
        with self.assertRaises(EOFError):
            read_frame(io.BytesIO(struct.pack('<I', 9) + b'abc'))
        with self.assertRaises(ValueError):
            read_frame(io.BytesIO(struct.pack('<I', MAX_FRAME + 1)))
        stream = io.BytesIO()
        write_frame(stream, b'valid frame')
        stream.seek(0)
        self.assertEqual(read_frame(stream), b'valid frame')

    def test_cycle_duration_is_subtracted(self):
        clock = [1000.0]
        cadence = viewer.SyncCadence(clock=lambda: clock[0])
        cadence.activate()
        clock[0] += 0.7
        self.assertAlmostEqual(cadence.wait(1000), 0.3)
        clock[0] += 2
        self.assertEqual(cadence.wait(1000), 0.1)


if __name__ == '__main__':
    unittest.main()
