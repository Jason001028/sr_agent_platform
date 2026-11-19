/** The exact transmitted instruction is also the identity of its compact rendering. */
import { AGENT_REPAIR_PROMPT, AGENT_REPAIR_DISPLAY_TEXT, AGENT_INSPECTION_PROMPT } from './repairPrompt';
const LEGACY_INSPECTION_PROMPT = '请仅对当前 Viewer 工作区进行质检，调用 inspect_repair_workspace，按返回的 next_cursor 分页完成检测并汇总候选、跳过项与未完成项；没有工作区时使用 current。不生成掩码、不准备修复计划、不启动或提交 SR。候选不等于已确认缺陷，缺少视觉证据时说明限制。';
// 已发送过的历史消息仍按原文落库：页大小从 4 调到 5 后，旧消息也应继续折叠成
// 同一句摘要，而不是因为一个数字对不上就展开全文。
const PRIOR_INSPECTION_PROMPT = AGENT_INSPECTION_PROMPT.replace('limit=5', 'limit=4');
export const ANALYZE_CURRENT_PROMPT = '请分析附件中的地貌特征，并在有视觉定位依据时调用空间批注工具，在影像上标出对应位置。使用实际附件像素坐标，不需要经纬度或投影；所有候选均待核实。若无法可靠定位，只返回文字并说明未获得可验证空间批注，不虚构坐标。';

export function analysisPromptSummary(text: string): string | null {
  if (text.trim() === AGENT_REPAIR_PROMPT || text.startsWith(AGENT_REPAIR_PROMPT+'\n本条消息设置：')) return AGENT_REPAIR_DISPLAY_TEXT;
  const instruction = text.trim().replace(/\n本条消息设置：检测方法=[^\n]+（(?:absdiff|ssim|smart)）；非线性归因权重=(?:开启|关闭)；自动跳过已有 PAN 备份的景=(?:开启|关闭)。$/, '');
  if (instruction === AGENT_INSPECTION_PROMPT || instruction === PRIOR_INSPECTION_PROMPT
      || instruction === LEGACY_INSPECTION_PROMPT) {
    return '对当前工作区仅做质检，不提交 SR。';
  }
  return text.trim() === ANALYZE_CURRENT_PROMPT
    ? '分析影像地貌，并标注可定位的位置'
    : null;
}
