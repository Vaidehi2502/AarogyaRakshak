# AarogyaRakshak

A provenance-aware enforcement gateway for AI agents calling EHR (electronic
health record) tools over MCP. The gateway sits between an agent and the EHR:
every tool call the agent makes is evaluated against a declarative purpose
policy *before* it reaches the EHR, and every EHR response is tainted with
provenance metadata before it flows back into the agent's context — so
untrusted data pulled from a clinical record can't quietly steer the agent
into an action it wasn't authorized for.

`enforce=False` produces an ungated baseline: the same calls with the
evaluator switched off, used only to measure what an attack achieves with no
gateway in front of the EHR at all.

## Results

60 scripted scenarios — 30 benign, 30 attacks across five classes (direct
injection, indirect injection, scope escalation, purpose drift, egress
exfiltration):

| class               | n  | accuracy | blocked | flagged | allowed | ungated success |
|---------------------|----|----------|---------|---------|---------|------------------|
| benign              | 30 | 100.0%   | 0       | 0       | 30      | 100.0%           |
| direct_injection    | 6  | 100.0%   | 6       | 0       | 0       | 100.0%           |
| egress_exfiltration | 6  | 100.0%   | 6       | 0       | 0       | 100.0%           |
| indirect_injection  | 6  | 100.0%   | 6       | 0       | 0       | 100.0%           |
| purpose_drift       | 6  | 100.0%   | 6       | 0       | 0       | 100.0%           |
| scope_escalation    | 6  | 100.0%   | 6       | 0       | 0       | 100.0%           |

- **Attack block rate:** 30/30 (100.0%)
- **False-positive rate (benign cases):** 0/30 (0.0%)
- **Ungated attack success rate:** 30/30 (100.0%) — i.e. every one of these
  attacks succeeds against the same EHR calls with the gateway removed.

**Scope:** these numbers measure the gateway's enforcement of scripted tool
calls, not whether an LLM agent can be talked into attempting them in the
first place — that's a separate, open question (see below).

Reproduce with:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python3 benchmark.py
```

## Architecture

- `gateway.py` — the enforcement gateway itself (MCP server to the agent,
  MCP client to the EHR).
- `evaluator.py` — the policy checks run on every call (purpose, scope,
  provenance taint, etc.); `provenance_taint` is a hard fail.
- `policy.py` — declarative purpose profiles; the only place permissions are
  defined.
- `provenance.py` — tags EHR responses with provenance so tainted data can be
  traced through the agent's context.
- `passport.py` / `issue_passport_cli.py` / `gateway_server.py` /
  `ehr_server.py` — a standalone, cross-process deployment: an agent and an
  EHR as separate OS processes talking through the gateway, with
  Ed25519-signed capability passports. `test_standalone_process.py` exercises
  this real process chain (the faster in-process harness in
  `test_scenarios.py`/`benchmark.py` is used for the scenario suite).
- `decision_feed.py` — a WebSocket feed of gateway decisions (ALLOW/BLOCK/FLAG)
  for a live console; `--fake` streams scripted events for demos.
- `scenarios/` — the 60 JSON scenario definitions behind the numbers above.

## Status / open items

- No LLM agent has been wired up yet to attempt these attacks autonomously —
  today's numbers are against scripted tool-call sequences.
- The latency numbers in `benchmark.py` are measured inside the gateway
  process (policy evaluation + upstream EHR call); they exclude the
  agent↔gateway transport leg, so they are not an end-to-end number.
