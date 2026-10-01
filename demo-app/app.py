"""Tiny login API used to demo SpecCheck.

Ticket: "After 3 failed login attempts, lock the account for 15 minutes."

This file plays the role of an AI-written PR. It contains a subtle bug:
the lock only triggers AFTER the 4th failure, so a correct password on
the 4th attempt still logs in. Set LOCKOUT_BUG=0 to run the fixed version.

Test hooks (only when TEST_HOOKS=1):
  POST /__test__/reset            -> clear all state
  POST /__test__/advance {"minutes": n} -> move the fake clock forward
"""

import json
import os
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(os.environ.get("PORT", "8000"))
TEST_HOOKS = os.environ.get("TEST_HOOKS") == "1"
LOCKOUT_BUG = os.environ.get("LOCKOUT_BUG", "1") == "1"

MAX_ATTEMPTS = 3
LOCK_MINUTES = 15
USERS = {"alice": "correct-horse"}

state = {"offset": timedelta(0), "failed": {}, "locked_until": {}}


def now():
    return datetime.now() + state["offset"]


def reset():
    state["offset"] = timedelta(0)
    state["failed"].clear()
    state["locked_until"].clear()


def login(username, password):
    locked_until = state["locked_until"].get(username)
    if locked_until and now() < locked_until:
        return 423, {"error": "account locked"}

    if USERS.get(username) == password:
        state["failed"][username] = 0
        return 200, {"ok": True}

    state["failed"][username] = state["failed"].get(username, 0) + 1
    # Bug: ">" should be ">=" (locks one attempt too late).
    limit_hit = (state["failed"][username] > MAX_ATTEMPTS) if LOCKOUT_BUG \
        else (state["failed"][username] >= MAX_ATTEMPTS)
    if limit_hit:
        state["locked_until"][username] = now() + timedelta(minutes=LOCK_MINUTES)
        state["failed"][username] = 0
    return 401, {"error": "invalid credentials"}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self):
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self._json()
        if self.path == "/login":
            return self._send(*login(body.get("username"), body.get("password")))
        if TEST_HOOKS and self.path == "/__test__/reset":
            reset()
            return self._send(200, {"ok": True})
        if TEST_HOOKS and self.path == "/__test__/advance":
            state["offset"] += timedelta(minutes=body.get("minutes", 0))
            return self._send(200, {"now": now().isoformat()})
        self._send(404, {"error": "not found"})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
