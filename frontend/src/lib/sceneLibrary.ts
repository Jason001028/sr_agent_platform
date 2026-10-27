import { joinBase, loadSrConfig } from './scene.js';

export const tagGroups = [
  { key: 'purpose', label: '任务用途', options: ['训练', '验证', '测试', '质检', '待修复'] },
  { key: 'content', label: '影像内容', options: ['建筑', '农田', '水体', '道路', '林地', '山地', '裸地', '混合场景'] },
  { key: 'quality', label: '质量问题', options: ['模糊', '噪声', '条带', '云雾', '阴影', '过曝', '欠曝'] },
  { key: 'custom', label: '自定义标签', options: [] },
] as const;
export type TagCategory = typeof tagGroups[number]['key'];
export type SceneTags = Record<TagCategory, string[]>;
export function emptyTags(): SceneTags { return { purpose: [], content: [], quality: [], custom: [] }; }

export async function addToSceneLibrary(entries: { scene_id: string; tags: SceneTags }[]): Promise<number> {
  const response = await fetch(joinBase(loadSrConfig().apiBase, '/api/scene-library'), {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ entries }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || `保存失败（HTTP ${response.status}）`);
  }
  return (await response.json()).count;
}
