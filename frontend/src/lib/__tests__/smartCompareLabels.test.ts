import {describe, expect, it} from 'vitest';
import {sceneEvidenceLabel, frequencyEvidenceLabel, confidenceEvidenceLabel, differenceSourceLabel} from '../smartCompareLabels';

describe('Smart Compare evidence wording', () => {
  it('never presents a missing or failed scene mask as excluded interference', () => {
    for (const status of ['unverified', 'unverified-source', 'unverified-read-failed', 'future-status']) {
      expect(sceneEvidenceLabel(status)).toContain('未排除干扰');
    }
    expect(sceneEvidenceLabel('available-disabled')).toContain('未降权');
    expect(confidenceEvidenceLabel('scene-checked')).toContain('待人工复核');
    expect(frequencyEvidenceLabel('reviewed')).toContain('实验性');
  });
  it('distinguishes a native-score overview from precise native ROI evidence', () => {
    expect(differenceSourceLabel('source-tiled')).toBe('原始尺度补扫概览');
    expect(differenceSourceLabel('source-roi')).toBe('原始 DN 精检');
    expect(differenceSourceLabel('preview')).toBe('预览检测');
  });
});
