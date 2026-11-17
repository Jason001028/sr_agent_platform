import { REPAIR_EXECUTION_MODE } from './repairExecution';

/** Stable full instruction: sent and persisted verbatim; only the bubble is summarized. */
export const AGENT_REPAIR_PROMPT =
  '请对当前 Viewer 工作区发起自动修复：先调用 prepare_repair'
  + `（scope=workspace，mode=${REPAIR_EXECUTION_MODE}；当前工作区为空时改用 current）准备真实自动修复计划并展示计划卡，`
  + '与「自动修复」使用同一执行链路；执行条件不满足时说明受阻，不切换模拟。'
  + '不要直接提交 SR。请说明目标景数、检测方法与掩码策略，'
  + '我会在计划卡上确认后启动。';
export const AGENT_REPAIR_DISPLAY_TEXT = '对当前工作区准备自动修复计划，确认后启动。';
