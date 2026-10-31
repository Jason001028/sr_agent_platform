/** The exact transmitted instruction is also the identity of its compact rendering. */
export const ANALYZE_CURRENT_PROMPT = '请分析附件中的地貌特征，并在有视觉定位依据时调用空间批注工具，在影像上标出对应位置。使用实际附件像素坐标，不需要经纬度或投影；所有候选均待核实。若无法可靠定位，只返回文字并说明未获得可验证空间批注，不虚构坐标。';

export function analysisPromptSummary(text: string): string | null {
  return text.trim() === ANALYZE_CURRENT_PROMPT
    ? '分析影像地貌，并标注可定位的位置'
    : null;
}
