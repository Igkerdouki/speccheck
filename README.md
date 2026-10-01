# SpecCheck

**Did the AI actually build what the ticket asked for? Don't read the code. Run it.**

AI code reviewers read the diff and guess whether a PR meets the ticket.
SpecCheck turns each requirement into real calls against the running app
(including fast-forwarding the clock), and posts a verdict with evidence:

| Verdict | Meaning |
|---|---|
| ✅ Met | The check ran and passed. Trace attached. |
| ❌ Not met | The check ran and failed. Trace shows exactly where. |
| ⚠️ Couldn't verify | The environment broke, or the requirement is too vague to test. |

## Example

Ticket: *"After 3 failed login attempts, lock the account for 15 minutes."*

The demo app (`demo-app/app.py`) is a plausible AI-written PR with an off-by-one bug.
SpecCheck catches it:

```
POST /login wrong        → 401  ✓
POST /login wrong        → 401  ✓
POST /login wrong        → 401  ✓
POST /login correct      → 200  (expected 423 ✗)
```

## Run it

```bash
# buggy PR → requirements fail, exit code 1
python3 speccheck/speccheck.py specs/login-lockout.json --start "python3 demo-app/app.py"

# fixed version → all pass
LOCKOUT_BUG=0 python3 speccheck/speccheck.py specs/login-lockout.json --start "python3 demo-app/app.py"
```

No dependencies, just Python 3 standard library.

## How it works

1. A spec (`specs/*.json`) lists the ticket's requirements as executable steps:
   HTTP calls, expected statuses, and `advance_minutes` to move a fake clock.
2. SpecCheck starts the app with `TEST_HOOKS=1`, runs every requirement from a clean state,
   and records every request and response.
3. It writes a Markdown report. The GitHub Action (`.github/workflows/speccheck.yml`)
   posts it on the PR and fails the check if any requirement is not met.

## Generate a spec from a ticket

Claude writes the spec from the ticket and an API description (`specs/api.json`).
It never sees the PR's code, so the checks reflect what was asked for, not what was built.

```bash
export ANTHROPIC_API_KEY=...   # or GROQ_API_KEY=... (free tier at console.groq.com)
python3 speccheck/generate.py "After 3 failed login attempts, lock the account for 15 minutes." \
  --api specs/api.json --out specs/login-lockout.draft.json
```

Review the draft, fix anything wrong, and save it without `.draft` to approve it.
Steps that use endpoints not in `api.json` are flagged before you approve.

## Roadmap

- [x] Generate the spec from the ticket text with an LLM (human approves it once)
- [ ] Database assertions (before/after state diff) with Postgres
- [ ] Read tickets from GitHub Issues / Linear
- [ ] Support a real framework (FastAPI or Express) instead of the stdlib demo
