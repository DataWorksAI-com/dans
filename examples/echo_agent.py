"""
echo_agent.py — Minimal A2A-compatible agent for local DANS testing.

Starts an HTTP server that accepts A2A message/send calls and echoes
the input back. Use it to test DANS registration, resolution, health
checks, protocol negotiation, and firewall rules without any real agents.

Usage:
    # Terminal 1 — start DANS
    docker compose up -d --build

    # Terminal 2 — start the echo agent
    python examples/echo_agent.py

    # Terminal 3 — register + resolve + call it
    python examples/demo.py
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

# Make startup banner safe on Windows consoles (cp1252) and everywhere else.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PORT   = 9001
LABEL  = "echo-agent"


class Handler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"  [echo-agent] {fmt % args}")

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok", "label": LABEL})
        elif self.path == "/.well-known/agent.json":
            self._json(200, {
                "name":        LABEL,
                "description": "Echo agent for DANS local testing",
                "version":     "1.0.0",
                "url":         f"http://localhost:{PORT}",
                "protocols":   ["a2a", "http"],
            })
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if self.path in ("/", "/a2a/message"):
            # Google A2A format — message/send
            if body.get("method") == "message/send":
                parts = body.get("params", {}).get("message", {}).get("parts", [])
                text  = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
                self._json(200, {
                    "jsonrpc": "2.0",
                    "id":      body.get("id", 1),
                    "result":  {
                        "kind":    "message",
                        "role":    "agent",
                        "parts":   [{"type": "text", "text": f"Echo: {text}"}],
                    },
                })
            else:
                self._json(200, {"jsonrpc": "2.0", "id": body.get("id", 1),
                                 "result": {"parts": [{"type": "text", "text": "Echo: (no message)"}]}})

        elif self.path == "/chat":
            # Custom HTTP format used by some agents
            msg = body.get("payload", {}).get("message", body.get("message", ""))
            self._json(200, {
                "type":    "response",
                "payload": {"text": f"Echo: {msg}"},
            })

        else:
            self._json(404, {"error": "not found"})

    def _json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Echo agent running on http://localhost:{PORT}")
    print(f"  GET  /health                 -> health check")
    print(f"  POST /                       -> A2A message/send")
    print(f"  POST /chat                   -> custom HTTP format")
    print(f"  GET  /.well-known/agent.json -> agent card")
    print(f"\nPress Ctrl+C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
