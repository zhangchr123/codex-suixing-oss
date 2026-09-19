"""Real TLS regression: stalled browser preconnections must not block the site."""
import http.client
import json
from pathlib import Path
import socket
import ssl
import tempfile
import threading
import unittest
import viewer

CERTIFICATE = Path(__file__).parent / "testdata/localhost-test-only.pem"


class TLSIsolationTests(unittest.TestCase):
    def test_stalled_and_partial_handshakes_leave_https_responsive(self):
        with tempfile.TemporaryDirectory() as folder:
            server = viewer.make_server("127.0.0.1", 0, Path(folder), "test-password-long-enough", tls=True)
            viewer.configure_tls(server, CERTIFICATE, CERTIFICATE)
            server.handshake_timeout = 0.5
            runner = threading.Thread(target=server.serve_forever, daemon=True)
            runner.start()
            stalled = []
            context = ssl.create_default_context(cafile=str(CERTIFICATE))
            def health():
                client = http.client.HTTPSConnection("127.0.0.1", server.server_port, context=context, timeout=2)
                try:
                    client.request("GET", "/health")
                    response = client.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertTrue(json.loads(response.read())["ok"])
                finally:
                    client.close()
            try:
                stalled.append(socket.create_connection(server.server_address, timeout=2))
                stalled.append(socket.create_connection(server.server_address, timeout=2))
                # A TLS record header whose payload never arrives.
                stalled[1].sendall(b"\x16\x03\x01\x00\x10")
                health()
                for connection in stalled:
                    self.assertEqual(connection.recv(1), b"")
                # Timed-out connections release their resources; normal traffic continues.
                health()
            finally:
                for connection in stalled:
                    connection.close()
                server.shutdown()
                server.server_close()
                runner.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
