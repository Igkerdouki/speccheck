"""Turn a ticket into a SpecCheck spec draft using Claude.

The AI sees only the ticket and the API description, never the PR's code,
so the checks reflect what was asked for, not what was built.

Works with Claude (ANTHROPIC_API_KEY) or Groq (GROQ_API_KEY).

Usage:
  export ANTHROPIC_API_KEY=...   # or GROQ_API_KEY=...
  python speccheck/generate.py "After 3 failed login attempts, lock the account for 15 minutes." \
      --api specs/api.json --out specs/login-lockout.draft.json

Review the draft, fix anything wrong, then save it without ".draft"
to approve it. SpecCheck runs approved specs on every PR.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
MODEL = os.environ.get("SPECCHECK_MODEL", "claude-sonnet-5")

STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "call": {"type": "string", "description": 'e.g. "POST /login"'},
        "json": {"type": "object", "description": "request body"},
        "expect_status": {"type": "integer"},
        "advance_minutes": {"type": "number", "description": "use instead of call to move the clock"},
    },
}

SPEC_SCHEMA = {
    "type": "object",
    "properties": {
        "ticket": {"type": "string"},
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "R1, R2, ..."},
                    "text": {"type": "string", "description": "one checkable statement"},
                    "steps": {"type": "array", "items": STEP_SCHEMA},
                    "untestable_reason": {"type": "string",
                                          "description": "only if steps is empty"},
                },
                "required": ["id", "text", "steps"],
            },
        },
    },
    "required": ["ticket", "requirements"],
}

PROMPT = """You write acceptance checks for a software ticket.

Ticket:
<ticket>
{ticket}
</ticket>

The app under test exposes exactly this API, test hooks and test data:
<api>
{api}
</api>

Split the ticket into separate, individually checkable requirements. Include
boundary cases that a buggy implementation would get wrong (for example "still
locked just before the limit" and "unlocked just after it").

For each requirement, write steps that use ONLY the calls, hooks and test data
listed above:
- Start every requirement with the reset hook so requirements are independent.
- Every step that tests behaviour must have expect_status.
- Use {{"advance_minutes": N}} to move time.

If a requirement is too vague to test with this API, give it an empty steps
list and explain why in untestable_reason. Do not invent endpoints.

Call write_spec with the result."""


def post(url, body, headers):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"content-type": "application/json",
                                          "user-agent": "speccheck", **headers})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"API error {e.code}: {e.read().decode()}")


def generate_groq(ticket, api, key):
    """Groq (OpenAI-compatible). JSON mode + the schema in the prompt."""
    prompt = PROMPT.format(ticket=ticket, api=json.dumps(api, indent=2)).replace(
        "Call write_spec with the result.",
        "Reply with ONLY a JSON object matching this schema:\n" + json.dumps(SPEC_SCHEMA))
    data = post("https://api.groq.com/openai/v1/chat/completions", {
        "model": os.environ.get("SPECCHECK_MODEL", "openai/gpt-oss-120b"),
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": prompt}],
    }, {"authorization": f"Bearer {key}"})
    try:
        return json.loads(data["choices"][0]["message"]["content"])
    except (KeyError, json.JSONDecodeError):
        sys.exit("Model did not return valid JSON.")


def generate(ticket, api):
    if not os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("GROQ_API_KEY"):
        return generate_groq(ticket, api, os.environ["GROQ_API_KEY"])
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("Set ANTHROPIC_API_KEY or GROQ_API_KEY first.")

    body = {
        "model": MODEL,
        "max_tokens": 4096,
        "tools": [{"name": "write_spec", "description": "Save the spec.",
                   "input_schema": SPEC_SCHEMA}],
        "tool_choice": {"type": "tool", "name": "write_spec"},
        "messages": [{"role": "user",
                      "content": PROMPT.format(ticket=ticket, api=json.dumps(api, indent=2))}],
    }
    data = post(API_URL, body, {"x-api-key": key, "anthropic-version": "2023-06-01"})
    for block in data["content"]:
        if block["type"] == "tool_use":
            return block["input"]
    sys.exit("Model did not return a spec.")


def validate(spec, api):
    """Reject steps that use endpoints the API doesn't have."""
    allowed = {e["call"] for e in api.get("endpoints", [])}
    allowed |= {h["call"] for h in api.get("test_hooks", []) if "call" in h}
    problems = []
    for r in spec["requirements"]:
        for i, step in enumerate(r["steps"], 1):
            if "advance_minutes" in step:
                continue
            if step.get("call") not in allowed:
                problems.append(f"{r['id']} step {i}: unknown call {step.get('call')!r}")
    return problems


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("ticket", help="ticket text, or @path/to/file.txt")
    p.add_argument("--api", required=True, help="API description JSON")
    p.add_argument("--out", required=True, help="where to write the draft spec")
    args = p.parse_args()

    ticket = open(args.ticket[1:]).read() if args.ticket.startswith("@") else args.ticket
    with open(args.api) as f:
        api = json.load(f)

    spec = generate(ticket, api)
    spec["ticket"] = ticket.strip()
    for i, r in enumerate(spec.get("requirements", []), 1):
        r.setdefault("id", f"R{i}")
        r.setdefault("steps", [])
    problems = validate(spec, api)

    with open(args.out, "w") as f:
        json.dump(spec, f, indent=2)
        f.write("\n")

    print(f"Draft written to {args.out}\n")
    for r in spec["requirements"]:
        mark = "⚠️ untestable" if not r["steps"] else f"{len(r['steps'])} steps"
        print(f"  {r['id']}: {r['text']}  [{mark}]")
        if r.get("untestable_reason"):
            print(f"      reason: {r['untestable_reason']}")
    if problems:
        print("\nFix before approving:")
        for line in problems:
            print("  - " + line)
    print("\nReview it, then save it without '.draft' to approve.")


if __name__ == "__main__":
    main()
