# Learnings

## [LRN-20261026-001] correction

**Logged**: 2026-10-26
**Priority**: critical
**Status**: resolved
**Area**: backend

### Summary
PAN repair uses original NOSR as model input; current PAN is the annotation and output grid.

### Details
Do not describe submitting SR as applying another enlargement to the latest PAN.
The original is `<full-production-name>_PAN_NOSR.tif` in the scene directory.
Model output is selected inside the mask, cubic NOSR interpolation outside.
Publish an equally sized PAN.tif and retain the previous bytes as PAN_old_2.tif.
The 260318-suffixed inference product is intermediate, not the final public name.

### Metadata
- Source: user_feedback
- Related Files: SR_code/variants/repair_sr.py, docs/sr_code/pan_repair_contract.md

## Tool result test fixtures

Category: best_practice. Large parametrized pytest payloads need short explicit IDs on Windows; automatic IDs can exceed temporary-path limits. Keep the payload intact and shorten only the test ID.

## 2026-10-27 commit 范围核对（correction）
- 用户指出提交说明遗漏数据集收集与 SR 逻辑。起草 commit 前先读取 git diff --cached --stat 与实际差异，按整个暂存区归纳，不能仅依据最近讨论的 P0-3。

## 2026-10-27 聊天阶段展示（correction）
- 当前模型阶段应复用助手消息里原有“推理中”的位置，不能把当前回合概览放在整个消息列表顶部。调试推理标签使用更浅的颜色、更小的字号，降低视觉权重。


## 2026-10-28 correction: viewer ASCII branding
White-matte source images must be converted to transparent masks for canvas overlays. User now requires persistent character flow with a stable brand silhouette, not a one-shot PNG reveal. Verify a recording spanning multiple cycles.

## 2026-10-28: Request deadline review correction
HTTP read timeout and per-chunk checks do not cancel a blocked read at an absolute deadline. Use an async HTTP task enclosed by one absolute budget, await stream/client closure, and verify peer-observed disconnect with real loopback TCP. Record available provider usage before rejecting late outputs; mark missing usage unknown. Model parameter acceptance is not evidence of thinking effectiveness.

## SSE ownership review

Category: best_practice. Execution ownership lasts until the actual Worker ends. Bound pending cross-thread callbacks as well as queued events. Completion idempotency uses a Run-local marker, not a cancellable Future state.

## SSE and usage review correction

Category: correction. Bounded observation queues must not carry the only final failure notification; send terminal results from the Worker result after completion. Persisted token subtotals must retain unknown_calls and usage_complete across Session queries and Goal checkpoints. Heartbeat tests should synchronize on observed successful renewals rather than sub-scheduler lease timings.

## 2026-10-29 spatial annotation corrections
- Internal HTTP deployments may not expose crypto.randomUUID; use an HTTP-compatible display ID generator.
- Optional grounding metadata must never break ordinary screenshot attachments.
- Render geometry per pane, while selecting a single popover by annotation ID and pane side.
- Persist confirmed bindings only to server resource revisions; local filenames and dimensions alone do not establish identity.
