import { REPAIR_EXECUTION_MODE } from './repairExecution';
import { agentRepairSettings, type AgentRepairSettings } from './agentRepairSettings';

/** Stable full instruction: sent and persisted verbatim; only the bubble is summarized. */
export const AGENT_REPAIR_PROMPT =
  '请对当前 Viewer 工作区发起自动修复：先调用 prepare_repair'
  + `（scope=workspace，mode=${REPAIR_EXECUTION_MODE}；当前工作区为空时改用 current）准备真实自动修复计划并展示计划卡，`
  + '与「自动修复」使用同一执行链路；执行条件不满足时说明受阻，不切换模拟。'
  + '不要直接提交 SR。请说明目标景数、检测方法与掩码策略，'
  + '我会在计划卡上确认后启动。';
export const AGENT_REPAIR_DISPLAY_TEXT = '对当前工作区准备自动修复计划，确认后启动。';
export const AGENT_INSPECTION_PROMPT = '请仅对当前 Viewer 工作区进行质检，调用 inspect_repair_workspace，使用 limit=5 从 cursor=0 按返回的 next_cursor 分页检测。界面在全部分页收齐后自动展示总表，请在检测结束后汇总候选、跳过项与未完成项；没有工作区时使用 current。不生成掩码、不准备修复计划、不启动或提交 SR。候选不等于已确认缺陷，缺少视觉证据时说明限制。';
export function buildAgentRepairPrompt(settings: AgentRepairSettings = agentRepairSettings.value): string {
  const method={absdiff:'绝对差异',ssim:'结构差异 SSIM',smart:'LTD 纹理退化'}[settings.detection_method];
  return (settings.inspection_only?AGENT_INSPECTION_PROMPT:AGENT_REPAIR_PROMPT)
    + `\n本条消息设置：检测方法=${method}（${settings.detection_method}）；非线性归因权重=${settings.tone_weight?'开启':'关闭'}；自动跳过已有 PAN 备份的景=${settings.skip_completed?'开启':'关闭'}。`;
}
