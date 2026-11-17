import { describe, it, expect } from 'vitest';
import { ANALYZE_CURRENT_PROMPT, analysisPromptSummary } from '../analysisPrompt';
import { agentPayload, splitAgentPayload } from '../agentContext';
import { AGENT_REPAIR_PROMPT, AGENT_REPAIR_DISPLAY_TEXT } from '../repairPrompt';

describe('automatic analysis instruction presentation', () => {
  it('summarizes repair instructions identically after persisted payload recovery', () => {
    const recovered = JSON.parse(JSON.stringify({content:agentPayload(AGENT_REPAIR_PROMPT,'workspace snapshot')}));
    const parts = splitAgentPayload(recovered.content);
    expect(parts.question).toBe(AGENT_REPAIR_PROMPT);
    expect(analysisPromptSummary(parts.question)).toBe(AGENT_REPAIR_DISPLAY_TEXT);
    expect(parts.question).toContain('mode=production');
    expect(parts.question).toContain('不要直接提交 SR');
    expect(parts.question).toContain('不切换模拟');
    expect(analysisPromptSummary(AGENT_REPAIR_PROMPT+' 仅处理第1景')).toBeNull();
  });
  it('summarizes the exact default while retaining the full transmitted question', () => {
    const payload = agentPayload(ANALYZE_CURRENT_PROMPT, 'context');
    const parts = splitAgentPayload(payload);
    expect(payload).toContain(ANALYZE_CURRENT_PROMPT);
    expect(analysisPromptSummary(ANALYZE_CURRENT_PROMPT)).toBe('分析影像地貌，并标注可定位的位置');
    expect(ANALYZE_CURRENT_PROMPT).toContain('不虚构坐标');
    expect(parts.question).toBe(ANALYZE_CURRENT_PROMPT);
    expect(parts.note).toBe('context');
  });
  it('never hides user additions or similar wording', () => {
    expect(analysisPromptSummary(ANALYZE_CURRENT_PROMPT + ' 请重点看河流。')).toBeNull();
    expect(analysisPromptSummary('分析影像地貌')).toBeNull();
    expect(analysisPromptSummary('')).toBeNull();
  });
});
