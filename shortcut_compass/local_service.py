"""Read-only loopback snapshots; Blender RNA is never accessed by server threads."""

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SnapshotService:
    def __init__(self, port, token):
        if not token:
            raise ValueError("A local access token is required")
        self.token = token
        self._lock = threading.Lock()
        self._payload = b'{"status":"starting","schema_version":1}'
        service = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                # Browsers have no reason to fetch this companion API.
                if self.headers.get("Origin"):
                    self.reply(403, b'{"error":"browser_origin_rejected"}')
                    return
                host = self.headers.get("Host", "")
                allowed = {f"127.0.0.1:{service.port}", f"localhost:{service.port}"}
                if host not in allowed:
                    self.reply(403, b'{"error":"invalid_host"}')
                    return
                supplied = self.headers.get("Authorization", "")
                if not secrets.compare_digest(supplied.encode("utf-8"), ("Bearer " + service.token).encode("utf-8")):
                    self.reply(401, b'{"error":"unauthorized"}')
                    return
                if self.path != "/v1/context":
                    self.reply(404, b'{"error":"not_found"}')
                    return
                with service._lock:
                    payload = service._payload
                self.reply(200, payload)

            def reply(self, status, payload):
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, _format, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", int(port)), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="ShortcutCompassSnapshot", daemon=True,
        )
        self.thread.start()

    def publish(self, snapshot):
        payload = json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode("utf-8")
        with self._lock:
            self._payload = payload

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
