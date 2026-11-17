export interface EvidenceAsset {
  file: string; role: string; sha256: string; width: number; height: number;
  reader: { actual_window: number[]; source_size: number[]; read_bytes: number };
  metrics: { valid_fraction: number; mean: number | null };
}
export interface EvidenceWindow { window_id: string; assets: EvidenceAsset[]; alignment_source: string; }
export interface EvidenceAnalysis { observation: string; recommendation: string; limitations: string; assessment: string; }
export interface EvidenceRow {
  row_id: string; item_id: string; review_id: string; region_id: string;
  plan: { scene_name?: string; coverage: string; windows: unknown[]; geometry?: { bbox?: number[] } };
  evidence_state: string; analysis_state: string; error_code: string | null;
  manifest: { windows?: EvidenceWindow[] }; analysis: EvidenceAnalysis | null;
}
export interface EvidenceReport {
  report_id: string; goal_id: string; revision: number; status: string; compute_state: string;
  created_at: number; evidence_counts: Record<string, number>; analysis_counts: Record<string, number>;
  coverage: { total_scenes: number; final_regions: number; analyzed: number; unanalyzed: number };
}
export interface EvidencePage { rows: EvidenceRow[]; total: number; report_total: number; offset: number; next_cursor: string | null; }
export class EvidenceApiError extends Error { constructor(public status: number, message: string) { super(message); } }
export async function evidenceFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let detail = '报告暂不可用，请稍后重试';
    try { detail = (await response.json()).detail ?? detail; } catch { /* Missing error body. */ }
    throw new EvidenceApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}
export const evidenceBase = (id: string) => `/api/repair-evidence-reports/${encodeURIComponent(id)}`;
export const evidenceStateLabel = (state: string) => ({ planned: '待生成', queued: '排队中', waiting_idle: '等待空闲', reading: '读取中', running: '分析中', ready: '已就绪', failed: '失败', stale: '来源已过期', unsupported: '不支持', unavailable: '模型不可用', not_requested: '暂未分析', partial: '部分完成', superseded: '历史版本', processing: '处理中' }[state] ?? state);
