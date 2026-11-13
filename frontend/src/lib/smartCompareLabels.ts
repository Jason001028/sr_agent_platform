/** Present evidence states without suggesting human confirmation or calibration. */
export function sceneEvidenceLabel(status: string): string {
  if (status === 'available') return '已验证 · 已降权';
  if (status === 'available-disabled') return '已验证 · 未降权';
  if (status.includes('read-failed')) return '读取失败 · 未排除干扰';
  if (status.includes('budget')) return '超出读取预算 · 未验证';
  if (status.includes('grid') || status.includes('registration')) return '配准未验证';
  return '未验证 · 未排除干扰';
}

export function frequencyEvidenceLabel(status: string): string {
  return ({disabled:'未启用', reviewed:'已复核（实验性）', 'insufficient-data':'有效纹理不足'} as Record<string,string>)[status] ?? '未验证';
}

export function confidenceEvidenceLabel(status: string): string {
  return ({low:'场景干扰重叠 · 低可信', 'scene-checked':'场景掩码已核对 · 待人工复核',
    'unverified-scene':'场景干扰未排除 · 待人工复核'} as Record<string,string>)[status] ?? '待人工复核';
}

export function differenceSourceLabel(source: string): string {
  return ({'source-roi':'原始 DN 精检', 'source-grid':'原始 DN 分析网格',
    'source-tiled':'原始尺度补扫概览', preview:'预览检测'} as Record<string,string>)[source] ?? '未知精度';
}
