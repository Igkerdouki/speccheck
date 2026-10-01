"""SpecCheck: run each requirement of a ticket against the real app and
report Met / Not met / Couldn't verify, with an HTTP trace as evidence.

Usage:
  python speccheck/speccheck.py specs/login-lockout.json \
      --base-url http://127.0.0.1:8000 \
      --start "python demo-app/app.py" \
      --report report.md

Exit code is 1 if any requirement is Not met, so it can gate a PR in CI.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

MET, NOT_MET, UNVERIFIED = "Met", "Not met", "Couldn't verify"
ICON = {MET: "✅", NOT_MET: "❌", UNVERIFIED: "⚠️"}


class EnvError(Exception):
    """The environment broke, so we can't judge the requirement."""


def http(base_url, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else b"{}"
    req = urllib.request.Request(base_url + path, data=data if method != "GET" else None,
                                 method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise EnvError(f"{method} {path} failed: {e}")


def run_requirement(base_url, req):
    if not req.get("steps"):
        return UNVERIFIED, ["Requirement is too vague to test. Rewrite it as a checkable criterion."]

    trace = []
    for step in req["steps"]:
        try:
            if "advance_minutes" in step:
                m = step["advance_minutes"]
                status, _ = http(base_url, "POST", "/__test__/advance", {"minutes": m})
                if status != 200:
                    raise EnvError("clock hook unavailable (start the app with TEST_HOOKS=1)")
                trace.append(f"⏩ clock +{m} min")
                continue

            method, path = step["call"].split(" ", 1)
            status, body = http(base_url, method, path, step.get("json"))
            expected = step.get("expect_status")
            line = f"{method} {path} {json.dumps(step['json']) if 'json' in step else ''}".rstrip()
            line += f" → {status} {body.strip()}"
            if expected is None:
                if status >= 400:
                    raise EnvError(f"setup call {method} {path} returned {status}")
                trace.append(line)
            elif status == expected:
                trace.append(line + f"  (expected {expected} ✓)")
            else:
                trace.append(line + f"  (expected {expected} ✗)")
                return NOT_MET, trace
        except EnvError as e:
            trace.append(f"environment error: {e}")
            return UNVERIFIED, trace
    return MET, trace


def render(spec, results):
    lines = ["# SpecCheck report", "", f"**Ticket:** {spec['ticket']}", "",
             "| | Requirement | Verdict |", "|---|---|---|"]
    for req, (verdict, _) in results:
        lines.append(f"| {req['id']} | {req['text']} | {ICON[verdict]} {verdict} |")
    lines.append("")
    for req, (verdict, trace) in results:
        lines += [f"<details><summary>{ICON[verdict]} {req['id']}: evidence</summary>", "",
                  "```", *trace, "```", "</details>", ""]
    return "\n".join(lines)


def wait_until_up(base_url, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            if http(base_url, "GET", "/health")[0] == 200:
                return True
        except EnvError:
            time.sleep(0.2)
    return False


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("spec")
    p.add_argument("--base-url", default="http://127.0.0.1:8000")
    p.add_argument("--start", help="command that starts the app under test")
    p.add_argument("--report", help="write the Markdown report to this file")
    args = p.parse_args()

    with open(args.spec) as f:
        spec = json.load(f)

    proc = None
    if args.start:
        env = {**os.environ, "TEST_HOOKS": "1"}
        proc = subprocess.Popen(shlex.split(args.start), env=env)
    try:
        if args.start and not wait_until_up(args.base_url):
            results = [(r, (UNVERIFIED, ["app did not start"])) for r in spec["requirements"]]
        else:
            results = [(r, run_requirement(args.base_url, r)) for r in spec["requirements"]]
    finally:
        if proc:
            proc.terminate()
            proc.wait()

    report = render(spec, results)
    print(report)
    if args.report:
        with open(args.report, "w") as f:
            f.write(report)
    sys.exit(1 if any(v == NOT_MET for _, (v, _) in results) else 0)


if __name__ == "__main__":
    main()
