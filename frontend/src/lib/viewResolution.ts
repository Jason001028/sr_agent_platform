export function overviewResolution(sourceW: number, sourceH: number, w: number, h: number) {
  const div = Math.max(sourceW / w, sourceH / h);
  return Number.isFinite(div) && div > 0
    ? `${w}×${h} · ${div === 1 ? '1:1' : `约 1/${Number(div.toFixed(2))}`}` : `${w}×${h}`;
}

export function tileResolution(
  scale: { x: number; y: number }, screenDpr: number, fitted: boolean,
  targetLOD: number, readyLOD: number | null, visible: number,
) {
  const x = scale.x * screenDpr, y = scale.y * screenDpr;
  const native = Math.abs(x - 1) < 1e-9 && Math.abs(y - 1) < 1e-9;
  const factor = (x + y) / 2;
  const n = (value: number) => Number(value.toPrecision(3));
  const ratio = factor < 1 ? `1:${n(1 / factor)}` : `${n(factor)}:1`;
  const view = native ? '原生 1:1' : `视图 ${ratio === '1:1' ? '约 ' : ''}${ratio}`;
  if (!visible) return `${view} · 影像外`;
  if (native && targetLOD === 0) return `${view} · ${readyLOD === 0 ? '已就绪' : '高清加载中'}`;
  if (fitted) return '全图概览 · 按需高清';
  return `${view} · L${targetLOD}${readyLOD === null ? ' 加载中' : ''}`;
}
