import { shallowRef } from 'vue';
import type { SceneSibling } from './api';
export interface EvidenceComparison { images: SceneSibling[]; scene_path: string; source_bbox: number[] | null; comparison_role: string; read_only: true; }
export const evidenceComparisonRequest = shallowRef<{ token: number; comparison: EvidenceComparison } | null>(null);
let nextToken=0;
export function requestEvidenceComparison(comparison: EvidenceComparison) { evidenceComparisonRequest.value = { token: ++nextToken, comparison }; }
