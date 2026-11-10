# Learnings

## 2026-10-30 correction: scene input identity

用户指出一键解析完整生产序列号_PAN.tif 被替换成裸 PAN.tif。根因是
input_scene_path 因 PAN 与完整序列号_NOSR 同时存在而抢占输入优先级。
恢复 input_candidates 的完整序列号优先顺序；NOSR 的存在不能作为切换浏览影像的信号。
回归必须包含完整序列号.tif、PAN.tif、完整序列号_NOSR.tif 三者共存，
检查文件身份、尺寸与掩码名，另保留只有 PAN 输入时的修复测试。

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
Publish an equally sized PAN.tif and retain the previous bytes as PAN_old_latest.tif
(the legacy PAN_old_2.tif is still read for scenes repaired before the rename).
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

## 2026-10-30 correction: shared scene list thumbnail
User confirms one existing scene _thumb.jpg may represent NOSR, PAN and ORI list cards. Preserve separate stage identities and detailed pixel sources. SR products were not included in this clarification.

## 2026-11-06 correction: compact batch rows and HTTP-safe start identity

The batch sequence selector should match QcListPanel's compact bordered rows (4px/7px padding, 3px gaps), with a single-line name and bottom-right rounded status badge. The user explicitly rejected large rows and redundant PAN scene counts. Retain anomaly explanations in accessible labels/tooltips.

Do not call crypto.randomUUID() in the intranet HTTP deployment. Reuse the existing spatialId() implementation for non-secret request identities, preserve the same frozen manifest/request_key on retry, and test actual start/selectGoal with randomUUID absent (also with crypto absent). Secure localhost browser tests alone did not cover this deployment constraint.

## 2026-11-07 correction: Batch PAN/NOSR aspect checks must match Viewer

Production PAN/NOSR dimensions include non-integer resampling and independent axis rounding (documented example: 30831×30948 / 15768×15823). Exact width/height cross-product equality in batch_difference rejected these valid production pairs even though Viewer already accepted them. Use the same symmetric relative aspect deviation and inclusive 1% tolerance as tourCompare/flicker; keep same-scene, frozen revision/resource and GeoTIFF contradiction checks independent. Test real non-integer dimension examples, boundary rejection and source-grid mask generation rather than only exact 2× square TIFF fixtures.

## 2026-11-07 correction: keep continuation navigation visible through transient states

DiffContinueButton used canContinueScene for v-if, conflating navigation availability with heat visibility and temporary compare/draw states. Separate visibility from disabled reasons. A reactive continuing flag must cover candidate loading, activation, analysis and result-layer decoding, including the move into the last scene; clear it only after the awaited operation settles. Test delayed stages and double clicks, not only the finished first/last scene screenshots.

## 2026-11-07 correction: missing tour metadata is not a negative PAN identity

Normal scene open/drop/ensureSceneImage paths can carry the authoritative input kind, Scene ID, revision and dimensions without tourIdentity. Auto-draw incorrectly treated that missing optional certificate as proof that only NOSR/SR was open. Accept a confirmed input among the analyzed A/B, retaining frozen autoMask source transforms and all supplied identity contradictions; revalidate current server kind, exact Scene ID, revision and dimensions before staging and before committing editable vectors. Do not redirect an NOSR/SR-only result to a third PAN merely because that file is active. Test both PAN A/B positions with absent local certificates against a server that does return certificates.

## 2026-11-05 correction: production tour registration evidence

The initial tour implementation assumed GeoTIFF tags were available on SR production outputs. `SR_code/util.py::writeTiff` creates TIFFs and writes pixels without setting a geotransform or projection. Scene identity, registration evidence and candidate eligibility must be reported separately. Display rejected candidates with reasons; never silently turn a registration refusal into an empty selector or infer verified alignment from same-directory names. Keep old and ori as distinct roles. Existing loaded Scene IDs may supply bounded candidates, but not substitute for registration proof.

## 2026-11-09 correction: shared work queues need caller-specific cancellation ownership

A thread-safe proxy around an async waiter does not give Batch cancellation ownership over the queued native job. Distinguish admission capacity from quality/execution errors; use a pending/running Future that removes cancelled queued work and refuses cancellation after native start. Measure execution from actual function entry, retain timed-out running records until native completion, and test both detection and submission verification against the real shared queue. Warm-cache checks must cover drop and temporary fallback paths. Fetch resolves at headers: keep navigation cancellation through complete body consumption and test an intentionally open ReadableStream, not only unresolved fetch promises.

## 2026-11-10: progressive preview ownership and sparse TIFF offsets

Keep provisional display pixels separate from authoritative DN and ROI inputs. Use a fixed scene-wide sampled stretch and receiver acknowledgement when publishing partial rows; publish completed pixels before optional JPEG export. Moving a whole-image loop into a Worker improves responsiveness but does not establish shorter decode time: measure first complete overview and final pixels separately. Read IFD tables and external arrays by validated file offsets; fixed prefix buffers cannot support a trailing BigTIFF IFD beyond 4 GiB.

## 2026-11-10 correction: navigation prepasses can regress X-drive loading

The user reports the progressive TIFF pipeline is slower in production. A separate overview decode, ACK backpressure on every patch, a full-size provisional canvas and an overly restrictive server-JPEG admission test added work. Synthetic local timings did not establish network-drive throughput. Prioritize a small subset of the final sparse sampling groups, reuse their DN, keep the original read set, make intermediate frames optional/rate-limited, and preserve the ordinary server-JPEG fast path. Do not force compressed sources through an independent navigation scan.

## 2026-11-10: server previews must not depend on business association

Chrome File inputs cannot expose a real client path. Use an explicitly installed native picker with callback-origin validation for mapped server reads, and bounded upload for inaccessible paths. Separate expiring preview assets from scene/SR authority. Keep original names in metadata and use short opaque storage names to handle Windows Unicode filenames on Linux. Protect queued raster jobs from cache cleanup after an HTTP consumer disconnects; keep disk quota scans outside the global ticket-status lock. Local synthetic upload speed does not establish production network-drive or multi-GiB throughput.

## 2026-11-10 correction: PowerShell overload binding overflows large file offsets

The native picker used Math.Max(0, largeOffset) and Math.Min(4096, fileLength). Windows PowerShell chose Int32 overloads from the literal first argument and failed before server-side preview on a user's 4.85 GB source. Explicitly cast both comparison arguments to Int64; only bounded buffer lengths may become Int32. Exercise the actual sampler function against a synthetic seekable stream above both 2 GiB and 4 GiB, checking offsets and bytes without allocating a giant file. Native fixes require rebuilding and reinstalling the client package.

## 2026-11-10 correction: diff candidate loading used a separate legacy path

The user clarified that slow second-image loading occurs in Diff Locate analysis, not ordinary file selection. Trace ensureCardPixels/fetchCardPixels, including the selection watcher, rather than assuming scene resolution. That path used full-size tier previews and a separate ready-only preparation round trip. Correction: the current backend returns JSON for ready-only, so buffering that response does not establish duplicate JPG transfer. Read the server contract before attributing costs. Threshold-only migration still left smaller ori/old candidates on the old path; unify all candidates on bounded assets, retaining only explicit 404 plus safe-size legacy fallback. Share each card's in-flight request and independent A/B controls. Preserve authoritative metadata and permissions. Report actual queue/render/download states so cold disk reads are distinguishable from a retry loop.
