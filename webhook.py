"""GitHub webhook receiver — auto-deploys on push to main."""

import hashlib
import hmac
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("WEBHOOK_PORT", 9000))
SECRET = os.environ.get("WEBHOOK_SECRET", "")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if SECRET:
            signature = self.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.HMAC(
                SECRET.encode(), body, hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(signature, expected):
                self.send_response(403)
                self.end_headers()
                return

        self.send_response(200)
        self.end_headers()

        subprocess.Popen(
            ["bash", "-c", f"cd {PROJECT_DIR} && git pull origin main && docker compose up -d --build"],
        )

    def log_message(self, format, *args):
        print(f"[webhook] {args[0]}")


if __name__ == "__main__":
    print(f"Listening on :{PORT}")
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    server.serve_forever()
