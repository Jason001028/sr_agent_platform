import { ref, watch } from 'vue';
import { defineStore } from 'pinia';
import { evidenceBase, evidenceFetch, EvidenceApiError } from '../lib/repairEvidence';
import type { EvidenceReport, EvidenceRow, EvidencePage, EvidenceAnalysisAttempt } from '../lib/repairEvidence';

export const useRepairEvidenceStore = defineStore('repairEvidence', () => {
  const reportId = ref('');
  const report = ref<EvidenceReport | null>(null);
  const rows = ref<EvidenceRow[]>([]);
  const offset = ref(0), total = ref(0), reportTotal = ref(0);
  const evidenceFilter = ref(''), analysisFilter = ref(''), sort = ref('scene');
  const loading = ref(false), error = ref('');
  const historyRowId = ref(''), history = ref<EvidenceAnalysisAttempt[]>([]);
  const historyLoading = ref(false), historyError = ref('');
  let historyController: AbortController | undefined, historyGeneration = 0;
  let generation = 0, controller: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let nextCursor: string | null = null;
  let active = false;

  function closeHistory() { historyGeneration++; historyController?.abort(); historyRowId.value = ''; history.value = []; historyLoading.value = false; historyError.value = ''; }
  function stop() { active = false; generation++; controller?.abort(); clearTimeout(timer); loading.value = false; closeHistory(); }
  async function loadHistory(rowId: string) {
    if (!active) return;
    const token = ++historyGeneration;
    historyController?.abort(); historyController = new AbortController();
    historyRowId.value = rowId; history.value = []; historyLoading.value = true; historyError.value = '';
    try {
      const result = await evidenceFetch<{ attempts: EvidenceAnalysisAttempt[] }>(`${evidenceBase(reportId.value)}/rows/${encodeURIComponent(rowId)}/history`, {signal:historyController.signal});
      if (active && token === historyGeneration) history.value = result.attempts;
    } catch(e) { if (active && token === historyGeneration) historyError.value = String(e); }
    finally { if (token === historyGeneration) historyLoading.value = false; }
  }
  function toggleHistory(rowId: string) { if (historyRowId.value === rowId) closeHistory(); else void loadHistory(rowId); }
  async function load(cursor?: string) {
    if (!active || !reportId.value) return;
    const token = ++generation;
    controller?.abort(); controller = new AbortController(); clearTimeout(timer); loading.value = true;
    const query = new URLSearchParams({ offset: String(offset.value), limit: '5', evidence_state: evidenceFilter.value, analysis_state: analysisFilter.value, sort: sort.value });
    if (cursor) query.set('cursor', cursor);
    try {
      const base = evidenceBase(reportId.value);
      const [snap, page] = await Promise.all([
        evidenceFetch<EvidenceReport>(base, { signal: controller.signal }),
        evidenceFetch<EvidencePage>(`${base}/rows?${query}`, { signal: controller.signal }),
      ]);
      if (token !== generation || !active) return;
      report.value = snap; rows.value = page.rows; offset.value = page.offset;
      total.value = page.total; reportTotal.value = page.report_total; nextCursor = page.next_cursor; error.value = '';
      const pending = (counts: Record<string, number>) => Object.entries(counts ?? {}).some(([state, count]) => count > 0 && ['planned','queued','waiting_idle','reading','running'].includes(state));
      if (pending(snap.evidence_counts) || pending(snap.analysis_counts)) timer = setTimeout(() => void load(), 5000);
    } catch (e) {
      if (token !== generation || !active) return;
      if (e instanceof EvidenceApiError && e.status === 409 && cursor) {
        offset.value = 0; rows.value = []; error.value = '报告状态已更新，已返回第一页';
        void load(); return;
      }
      if (!(e instanceof DOMException && e.name === 'AbortError')) error.value = String(e);
    } finally { if (token === generation) loading.value = false; }
  }
  function start(id: string) { stop(); active = true; reportId.value = id; report.value = null; rows.value = []; offset.value = 0; nextCursor = null; void load(); }
  function turn(delta: number) { if (loading.value) return; closeHistory(); offset.value = Math.max(0, offset.value + delta); rows.value = []; void load(delta > 0 ? nextCursor ?? undefined : undefined); }
  watch([evidenceFilter, analysisFilter, sort], () => { closeHistory(); offset.value = 0; rows.value = []; nextCursor = null; void load(); });
  return { reportId, report, rows, offset, total, reportTotal, evidenceFilter, analysisFilter, sort, loading, error, start, stop, load, turn,
    historyRowId, history, historyLoading, historyError, toggleHistory, loadHistory };
});
