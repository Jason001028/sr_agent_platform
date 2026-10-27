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
