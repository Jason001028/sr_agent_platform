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

