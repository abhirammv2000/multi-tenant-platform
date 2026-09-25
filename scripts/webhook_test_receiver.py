"""A standalone HTTP receiver for verifying the platform's webhook signing end-to-end. It
recomputes the HMAC the same way a tenant's receiver would, and rejects what it should
reject. Run standalone:

    python scripts/webhook_test_receiver.py <signing_secret> [port]

Keeps no state of its own, just prints ACCEPT/REJECT with the reason for every request
(compare against the WebhookDelivery rows for what was actually signed and when).
"""
import sys
import hmac
import hashlib
import time
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

REPLAY_WINDOW_SECONDS=300 #matches shared/config.py's WEBHOOK_REPLAY_WINDOW_SECONDS default


def make_handler(signing_secret: str):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length=int(self.headers.get("Content-Length", 0))
            raw_body=self.rfile.read(length)

            sig_header=self.headers.get("X-Platform-Signature", "")
            ts_header=self.headers.get("X-Platform-Timestamp", "")

            if not sig_header.startswith("sha256="):
                return self._reject("missing/malformed X-Platform-Signature header")
            provided_sig=sig_header[len("sha256="):]

            if not ts_header.isdigit():
                return self._reject("missing/malformed X-Platform-Timestamp header")
            timestamp=int(ts_header)

            now=int(time.time())
            if abs(now-timestamp)>REPLAY_WINDOW_SECONDS:
                return self._reject(f"timestamp {timestamp} is outside the {REPLAY_WINDOW_SECONDS}s replay window (now={now})")

            message=f"{timestamp}.".encode()+raw_body
            expected_sig=hmac.new(signing_secret.encode(), message, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(provided_sig, expected_sig):
                return self._reject("signature mismatch, payload or timestamp was tampered with")

            try:
                payload=json.loads(raw_body)
            except Exception:
                payload=raw_body
            print(f"ACCEPT: {payload}")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def _reject(self, reason):
            print(f"REJECT: {reason}")
            self.send_response(401)
            self.end_headers()
            self.wfile.write(reason.encode())

        def log_message(self, format, *args):
            pass #keep stdout to just ACCEPT/REJECT lines

    return Handler


if __name__=="__main__":
    secret=sys.argv[1]
    port=int(sys.argv[2]) if len(sys.argv)>2 else 9000
    server=HTTPServer(("127.0.0.1", port), make_handler(secret))
    print(f"webhook test receiver listening on 127.0.0.1:{port}")
    server.serve_forever()
