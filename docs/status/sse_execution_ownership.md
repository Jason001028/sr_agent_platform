# SSE execution ownership and request observability

> Date: 2026-10-28. Status: implemented; process acceptance has unresolved failures.

## Ownership

`chat_send -> acquire Session asyncio.Lock -> save attachments -> start Worker
thread -> run_loop -> completion callback -> release Session lock`.
Acquisition and release both run on the API event loop. Each Run closure owns an
idempotent completion marker, independently of the completion Future's cancellation
state. Thread startup failure releases the lock. SSE closure only detaches and
clears the subscriber queue. Same-Session requests return 409 while the Worker runs;
other Sessions proceed. The Worker starts before the first SSE event is consumed.

The subscriber queue and pending cross-thread callbacks each have a limit of 256.
Publishing never blocks the Worker. Detached subscribers discard observation events.
Full queues can lose live presentation events; Store messages, tool facts and
Trajectory persistence are independent of delivery. Reconnect uses existing history
and conflict semantics, without event replay. Normal shutdown awaits active ordinary
chat Workers before closing Store.

Durable Goal workers share the in-process Session lock. Their business lifecycle
continues to use persisted Goals, Checkpoints, Intent and Lease Fencing. Goal SSE
only observes it. Direct REST tool calls are outside the ordinary-chat Run boundary.

## Usage and diagnostics

`run_loop -> _default_chat -> async_transport.request -> AsyncOpenAI.create`.
Model reservation and actual transport attempts are separate observations. The
fallback callback reserves another model call; diagnostics counts actual attempted
async dispatches. Image hydration and budget/preflight failures before dispatch
have zero observed attempts. Legacy injected chat functions without diagnostics
use their invocation count as the observation boundary.

Usage accounting precedes response rejection and exception handling. A trusted
Provider Usage records one known call. Other attempted requests increment
unknown_calls and emit a Trajectory usage event with observation
send_attempted_usage_unknown. This does not assert remote token consumption.
Fallback success accounts for the rejected attempt as unknown and the final
Provider Usage as known, without double counting.

Shared request diagnostics preserve capability_rejections even if fallback budget
checks, connection failures or deadlines prevent a normal response. Each logical
model invocation has one llm_request_end containing original request_config,
request_attempts and effective_thinking_parameters. Thinking parameter acceptance
remains unverified; it does not prove the model applied the requested control.

## Verification and limits

Tests: test_sse_ownership.py (real worker races and actual Loop with blocked chat),
test_request_budget.py (usage and exceptional diagnostics), test_async_transport.py
(real TCP fallback timeout/disconnect/cancellation and deadline closure).
Latest focused run: 40 passed. Joint regression: 335 passed, 2 skipped, 2 failed
and 1 teardown error. Goal process-only retry with subprocess permissions:
1 passed, 2 failed (model startup wait and completion wait); termination cleanup
succeeds in that retry. These process acceptance failures remain unresolved.
Commands and raw results are in .e2e/sse-closure-*.log and the delivery report.

The lock protects one process and one API event loop, not multiple processes.
Daemon Workers can stop on forced process exit. Ordinary chat has no new durable
restart protocol. Normal shutdown can wait indefinitely for a tool that itself
blocks indefinitely; forced process-manager termination retains that boundary.
Goal leases remain the durable recovery mechanism.

Real models, intranet SR/GPU, Qwen/llama.cpp thinking effectiveness, and provider
resource reclamation after cancellation have not been verified. SSE disconnection
intentionally leaves inference running. Missing Provider Usage prevents exact token
accounting. Existing deadline, integrity, schema, Intent, Lease, Verifier, tool
result budget and message-frozen Viewer context protections remain in place.

## Changed files and reproducible commands

- backend/api/platform.py: Worker-owned cleanup, bounded detached subscription,
  startup/exception cleanup and same-Session conflict behavior.
- backend/api/app.py: await ordinary chat completion before Store shutdown.
- backend/agent/loop.py: shared request diagnostics and unified usage accounting.
- backend/agent/async_transport.py: attempted dispatch counts and durable diagnostic
  observations across asynchronous failures. This file was already untracked in
  the input working tree; it is extended here, not a new runtime.
- backend/tests/test_sse_ownership.py: new lifecycle/concurrency test module.
- backend/tests/test_request_budget.py, test_async_transport.py: extended existing
  untracked tests for exceptions, accounting and TCP fallback failures.
- docs/status/current-question.md: handoff link; this document: operational report.
- .learnings/LEARNINGS.md and ERRORS.md: skill-required review observations.

Joint command (335 passed, 2 skipped, 2 failed, 1 teardown error; 232.32s):

```powershell
python -m pytest backend/tests/test_request_budget.py backend/tests/test_async_transport.py backend/tests/test_sse_ownership.py backend/tests/test_agent_loop.py backend/tests/test_runtime_status.py backend/tests/test_api_platform.py backend/tests/test_chat_images.py backend/tests/test_message_viewer_context.py backend/tests/test_goal_runtime.py backend/tests/test_goal_clarification.py backend/tests/test_goal_process.py backend/tests/test_goal_local_restart.py backend/tests/test_tool_result_formatter.py -q -o faulthandler_timeout=60
```

Final focused command (40 passed, zero failures/skips; 24.89s):

```powershell
python -m pytest backend/tests/test_sse_ownership.py backend/tests/test_request_budget.py backend/tests/test_async_transport.py -q -rs
```

Final ownership-only command after exceptional session-status persistence
(4 passed, zero failures/skips; 16.81s):

```powershell
python -m pytest backend/tests/test_sse_ownership.py -q
```

Process acceptance with subprocess permissions (1 passed, 2 failed; 65.93s):

```powershell
python -m pytest backend/tests/test_goal_process.py -q -rs
```

Diagnostic repeat: 1 passed, 2 failed; 62.34s. At the five-second startup deadline
one Goal was still QUEUED with zero model reservations. The completion failure
exposed an external_result rejecting the fixture's missing
Debug/_SRREPAIR_*.json PAN audit file. These are observed failure conditions;
no baseline equivalence or successful process acceptance is claimed.
The restricted-run teardown error was a Windows taskkill timeout; the permitted
retry terminated its children normally. The two joint skips are Windows chmod
permission-bit semantics and absence of a real Bash for local restart acceptance.

The initial incorrectly named test module command collected no tests and is not
counted as validation. An interrupted early joint attempt is also not counted.
No Git commit, production SR submission or production-data update was performed.
The input working tree's unrelated changes are retained. git diff --check passes.

## P2 closure verification

Final SSE success and failure frames are emitted directly from result_box after
Worker completion. Error bridge events are retained as a fallback error message,
not sent through the lossy queue, so a failed Run sends one final error before EOF.
Success still sends turn_done with the final answer. A deterministic test waits
for Worker completion before consuming 2,000 generated deltas and asserts exactly
one terminal event for success and failure.

Session summarize_llm_usage aggregates unknown usage events directly from the
persisted trajectory with no list pagination limit or new usage ledger. Its
unknown_calls and usage_complete survive Store closure/reopening. Goal checkpoint
usage accumulates unknown_calls across rounds, and incomplete status stays false
when later rounds have known Usage. Token fields remain known subtotals; calls
retains its established known-Usage-call count. Existing Goal checkpoints written
without completeness fields are not retroactively reconstructed.

Files additionally affected: backend/services/store.py,
backend/services/goal_worker.py, backend/tests/test_goal_runtime.py. The heartbeat
test blocks model completion until four successful renewals have been observed
under a 1.5-second Lease; it then verifies tool execution. Production heartbeat and
Lease settings are unchanged.

Current validation:

```powershell
python -m pytest backend/tests/test_sse_ownership.py backend/tests/test_request_budget.py backend/tests/test_goal_runtime.py -q
# 65 passed in 47.86s
python -m pytest backend/tests/test_async_transport.py backend/tests/test_agent_loop.py backend/tests/test_runtime_status.py backend/tests/test_api_platform.py backend/tests/test_message_viewer_context.py backend/tests/test_goal_clarification.py -q -rs
# 213 passed, 1 skipped in 119.34s
```

The skip is Windows chmod permission-bit semantics. An earlier focused run had
64 passes and one heartbeat failure: a 281 ms event-loop delay exceeded the old
120 ms test Lease. That failure is retained here; the synchronized test replaces
that timing assumption. The previous process-level acceptance failures remain
unresolved and were not rerun for these P2 changes. Real ASGI network disconnect,
production SR/GPU and provider-side inference termination remain unverified.
