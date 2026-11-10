export interface ImageSize { W:number;H:number }

/** 「真分辨率」长边上限的默认值：长边 4096 × 4 = 16384。
 *
 *  与 Chromium 画布 16384² 的面积上限同口径：预览/解码的实际长边不超过它，就按
 *  **该档位的真实采样**交付；超过则浏览器必须截断，所以服务端生成与前端解码都在
 *  这里封顶。1:1 档位在源图长边 ≤16384 时才是真 1:1。
 *
 *  实际生效值来自平台设置（服务端持久化，所有机器共用，见 `SettingsPanel`）；
 *  这个常量只是加载失败时的安全默认与选项之一。 */
export const PREVIEW_TRUE_MAX_EDGE = 16384;
/** 设置面板提供的三档。顺序从省内存到最清晰；默认 16384。 */
export const PREVIEW_TRUE_MAX_EDGE_OPTIONS = [4096, 8192, 16384] as const;

let currentPreviewTrueMaxEdge = PREVIEW_TRUE_MAX_EDGE;
/** 是否成功读过服务端设置。没同步过时缓存签名复核不按上限判 —— 宁可多显示
 *  一张已知档位的图，也不要因为「本页还没拉到设置」把所有缩略图判成无效。 */
let settingsSynced = false;

/** 当前生效的长边上限（服务端设置同步后由 `applyPreviewTrueMaxEdge` 改写）。 */
export function getPreviewTrueMaxEdge(): number {
  return currentPreviewTrueMaxEdge;
}

/** 这一页是否已经拿到过服务端设置（`main.ts` 启动时同步一次）。 */
export function isPreviewMaxEdgeSynced(): boolean {
  return settingsSynced;
}

/** 应用服务端设置；非法值忽略并保持原值，返回是否生效。 */
export function applyPreviewTrueMaxEdge(edge: number): boolean {
  if (!(PREVIEW_TRUE_MAX_EDGE_OPTIONS as readonly number[]).includes(edge)) return false;
  currentPreviewTrueMaxEdge = edge;
  settingsSynced = true;
  return true;
}

/** 上限对应的最大像素数（16384² = Chromium 画布面积上限）。 */
export function previewTrueMaxPixels(): number {
  return currentPreviewTrueMaxEdge * currentPreviewTrueMaxEdge;
}

export function isTruePreviewEdge(edge:number) {
  return Number.isFinite(edge) && edge > 0 && edge <= currentPreviewTrueMaxEdge;
}

/** 状态栏统一口径：`实际约 1/3.2（长边 15360，真分辨率）`。
 *  实际长边超过上限时如实写「超过 N 上限」，不再让多个档位看起来一样。 */
export function previewResolutionNote(sourceW:number,sourceH:number,width:number,height:number) {
  if(![sourceW,sourceH,width,height].every(n=>Number.isFinite(n)&&n>0))return '';
  const edge = Math.max(width,height);
  const ratio = Math.max(sourceW / width, sourceH / height);
  const limit = currentPreviewTrueMaxEdge;
  return `实际约 1/${Number(ratio.toFixed(2))}（长边 ${edge}，${isTruePreviewEdge(edge)?'真分辨率':`超过 ${limit} 上限`}）`;
}

/** Only authoritative same-scene dimensions can opt NOSR into a sharper tier. */
export function imagePreviewDiv(selected:number,kind:string|undefined,image:ImageSize,pan?:ImageSize) {
  if(kind!=='nosr'||!pan||![image.W,image.H,pan.W,pan.H].every(n=>Number.isFinite(n)&&n>0))return selected;
  if(image.W>pan.W||image.H>pan.H||(image.W===pan.W&&image.H===pan.H))return selected;
  return selected<=2?1:2;
}
