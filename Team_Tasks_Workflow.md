# AarogyaRakshak — Team Tasks & Workflow

Five people. Each person reads their own section. Everyone reads "Shared rules" at the bottom.

| Who | Owns | Never touches |
|---|---|---|
| **V** (Vaidehi) | MCP EHR server, gateway, evaluator, passports, provenance, audit log | Console CSS, agent prompts |
| **A1** | Agent runtime, tool-calling loop, cached replay | Gateway internals |
| **A2 + A3** | 60 scenarios, benchmark harness, attack suite, metrics | Gateway internals |
| **A4** | Console — ledger, provenance pane, incident replay, states | Anything server-side |

---

# V — Security core

## Block 1 · Unblock everyone (first 90 min, ship in this order)

**T1. EHR MCP server, stubbed** — 30 min
Four tools from `contracts/tools.json`, hardcoded dict returns, no logic.
*Done when:* `python ehr_server.py` runs and an MCP client can list and call all four tools.
*Tell A1 immediately.*

**T2. Pass-through gateway** — 45 min
MCP server facing the agent, MCP client facing the EHR. `list_tools` forwards upstream unchanged. `call_tool` forwards, logs, returns. No checks yet.
*Done when:* the agent calls a tool through the gateway and gets the real result back.
*Tell A1 immediately — they repoint the agent.*

**T3. `--fake` decision emitter** — 20 min
Emits scripted `decision.json` events over WebSocket on a 3s timer: one allow, one block, one flag.
*Done when:* A4 can `npm run dev` and see events arrive. *Tell A4 immediately.*

**After T3 nobody is blocked on you until integration.**

## Block 2 · Real enforcement

**T4. Passport issuer + verifier** — Ed25519, canonical JSON signing, expiry. 1.5 hr
**T5. Checks 1–5** — signature, identity, tool, subject, volume. Each returns a named verdict object matching `contracts/decision.json`. 2 hr
*Done when:* scope escalation blocks and the ledger names check 4.

**T6. Real EHR data** — replace stubs with 8 synthetic patients, structured fields plus one free-text `notes` per patient. One carries the injection; two carry benign odd text. 1 hr

**T7. Provenance tagging** — 2.5 hr, the hard one
Tag on the **response** path, not the request path. When the gateway returns a tool result to the agent, that's when untrusted content enters context. Keep a per-session map of `{text_span → source_uri}`; check action parameters against it.
*Done when:* the exfiltration attempt blocks and the ledger names `ehr:P-102/notes[412–487]`.

**T8. Checks 6–8** — purpose profile, egress allowlist, provenance. 1.5 hr
**T9. Signed audit log** — append-only, hash-chained. 45 min

## Block 3 · Integration
**T10.** Swap A4's `--fake` feed for real decisions. Swap A2/A3's harness onto the real gateway.

---

# A1 — Agent runtime

## Before V ships T1 (start now, needs nothing)

**T1. Tool-calling loop scaffolding** — 1 hr
Read `contracts/tools.json`. Build the loop: user message → LLM → tool call → result → LLM → response. Stub the tool executor to return fixed dicts.

**T2. Cache record/replay** — 1.5 hr
Every LLM request/response pair written to `cache/<hash>.json`. A `--replay` flag reads from cache and makes zero network calls.
**This is non-negotiable and it is your most important deliverable.** Venue wifi will fail. This is what saves the demo.

**T3. ASHA assistant system prompt** — 30 min
Health worker assistant, uses the four tools, does not mention security or gateways. It must behave like a normal agent — if it's prompted to be suspicious, the whole demo is invalid.

## After V ships T1/T2
**T4.** Point the executor at the EHR server, then at the gateway. 30 min
**T5.** Handle refusals gracefully — a blocked call produces a sensible user-facing message, not a crash or a retry loop. 45 min
**T6.** Deterministic seeding — same scenario, same tool calls, every run. 30 min

*Done when:* `python agent.py --replay --scenario egress_exfil_03` produces identical output twice with the network off.

---

# A2 + A3 — Benchmark & attack suite

You two are the highest-leverage pair after V. Your output is the metrics slide.

## Phase 1 · Write scenarios (start now, no code needed, ~2.5 hr)

Split: **A2 takes benign + scope escalation + purpose drift. A3 takes the three injection classes.**

**30 benign workflows.** Realistic tasks that must pass. Spread across all four tools. These produce your false-positive number, so make them genuinely varied — not 30 variations of one call.

**30 attacks, 6 per class:**
- direct injection
- indirect injection via record content
- scope escalation
- purpose drift
- egress exfiltration

Write each as `contracts/scenario.json`. All 60 files before any harness code.

## Phase 2 · Harness (A2, ~2 hr)
Load scenario → issue passport → run agent → collect decisions → compare to `expected`.
Output: block rate, false-positive rate, median and p95 latency, per-class breakdown.

## Phase 3 · Adversarial validation (A3, ~2 hr) — **do not skip**

Run the full suite with **enforcement disabled**. If your attacks don't succeed against the ungated agent, they prove nothing, and a judge asking "would that have worked anyway?" has you.

Any attack that fails against the ungated agent gets rewritten until it lands. Make them plausible: fake clinical-workflow framing, instructions inside structured-looking text, multi-step chains where each step reads as reasonable. "Ignore previous instructions" will not work on a current model and will make your suite look naive.

*Done when:* you can state the ungated attack success rate. That number belongs on the slide next to your block rate.

---

# A4 — Console

## Before V ships T3 (start now)

**T1. Design tokens file** — 30 min
Straight from the design spec. Six colours, five type sizes, two weights, 4px spacing scale. Do this before any component.

**T2. Static layout, hardcoded data** — 2 hr
Three panes: scenario list left, check ledger centre, context provenance right.

## After V ships T3 (`--fake` feed)

**T3. Wire the WebSocket** — 1 hr. Render decisions as they arrive. No polling.
**T4. Provenance pane** — 1.5 hr. TRUSTED / STRUCTURED / UNTRUSTED tags per field.
**T5. The sever animation** — 3 hr. Line from the tainted parameter to its origin span, pulse, snap, ledger rows stamp in 60ms apart, verdict slides up. Under 1.2s total. SVG and CSS transforms only.
**T6. The five states** — empty, loading, allow, error, focus. 1 hr. Most teams skip these; they're what separates a demo from a product.
**T7. Incident replay** — 2 hr. Scrub the timeline, highlight the injected span.

*Done when:* the console looks correct with nothing running, and correct at 3 metres on a projector.

---

# Shared rules

**Contracts are law.** Need a field that isn't in a contract file? Change the contract file and tell everyone in the group. Never add it locally.

**Branch per stream.** `v/gateway`, `a1/agent`, `a2/bench`, `a4/console`. Merge to `main` only at integration checkpoints. Nobody force-pushes `main`.

**Integration checkpoints.** Everyone stops and merges at three moments:
1. First end-to-end tool call through the gateway
2. First real block with a named failing check
3. First full 60-case run

**Standup every 3 hours.** Three sentences each: what's done, what's next, what's blocking. Under five minutes total.

**V does not debug other streams.** If the console breaks, that's A4's. The evaluator is the only component nobody else can write.

**Cut order if you fall behind:** incident replay → `update_record` and `read_allergy` → scenario count 60→30 (and say so on the slide) → console polish.
**Never cut:** the eight checks, provenance tagging, cached replay mode.
