export interface ImageSize { W:number;H:number }

/** 「真分辨率」长边上限的默认值：32768。
 *
 *  预览/解码的实际长边不超过它，就按**该档位的真实采样**交付；超过则浏览器必须
 *  截断，所以服务端生成与前端解码都在这里封顶。32768 只放宽长边：整幅面积仍受
 *  Chromium 16384² 画布上限约束，所以 1:1 档位在源图长边 ≤32768、且面积不超画布
 *  时才是真 1:1（接近方形的大图会按面积退回更小的长边）。
 *
 *  实际生效值来自平台设置（服务端持久化，所有机器共用，见 `SettingsPanel`）；
 *  这个常量只是加载失败时的安全默认与选项之一。 */
export const PREVIEW_TRUE_MAX_EDGE = 32768;
/** Chromium 画布面积上限（16384²）。长边选项可以放宽到 32768，但单张整幅画布
 *  的面积不能越过它；窄长图（如 32768×8192）可行，接近方形的大图仍要按面积降采样。 */
export const PREVIEW_CANVAS_AREA_MAX = 16384 * 16384;
/** 设置面板提供的四档。顺序从省内存到最清晰；默认 32768。32768 只对整幅面积
 *  不超过画布上限的长图有实际意义。 */
export const PREVIEW_TRUE_MAX_EDGE_OPTIONS = [4096, 8192, 16384, 32768] as const;

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

/** 整幅预览允许的最大像素数：长边上限²，但不超过 Chromium 画布面积上限。
 *  32768 档下这一步把面积钉在 16384²，而不是 32768²。 */
export function previewTrueMaxPixels(): number {
  return Math.min(currentPreviewTrueMaxEdge * currentPreviewTrueMaxEdge,
    PREVIEW_CANVAS_AREA_MAX);
}

export function isTruePreviewEdge(edge:number) {
  return Number.isFinite(edge) && edge > 0 && edge <= currentPreviewTrueMaxEdge;
}

/** 状态栏统一口径：`实际约 1/3.2（长边 15360，真分辨率）`。
 *  实际长边超过上限时如实写「超过 N 上限」。
 *
 *  32768 档下还有第三种情况：**长边没到上限，但整幅面积先撞上 Chromium
 *  16384² 画布上限**。例如 49475×49454 选 1:1，长边只能生成到 16387，实际是
 *  约 1/3.02；它和 1/2 档的产物尺寸相同。这时不能说「真分辨率」，否则用户会
 *  以为 1:1 已经给了原尺寸。判据是长边已达到面积公式允许的最大值，且整幅面积
 *  落在画布上限内（超过上限的异常尺寸不冒充「受限制」）。 */
export function previewResolutionNote(sourceW:number,sourceH:number,width:number,height:number, limits?: readonly string[]) {
  if(![sourceW,sourceH,width,height].every(n=>Number.isFinite(n)&&n>0))return '';
  const edge = Math.max(width,height);
  const ratio = Math.max(sourceW / width, sourceH / height);
  if (limits) {
    const reason = limits.includes('area') ? '受画布面积限制'
      : limits.includes('memory') ? '受本地预览内存限制'
      : limits.includes('long-edge') ? '受长边限制'
      : limits.includes('reader') ? '读取器交付尺寸'
      : ratio === 1 ? '原图像素' : '档位分辨率';
    return `实际约 1/${Number(ratio.toFixed(2))}（长边 ${edge}，${reason}）`;
  }
  const limit = currentPreviewTrueMaxEdge;
  const longEdge = Math.max(sourceW, sourceH), shortEdge = Math.min(sourceW, sourceH);
  const areaEdge = Math.max(1, Math.floor(
    Math.sqrt(PREVIEW_CANVAS_AREA_MAX * longEdge / shortEdge)));
  const areaLimited = limit * limit > PREVIEW_CANVAS_AREA_MAX
    && longEdge > areaEdge && edge >= areaEdge
    && width * height <= PREVIEW_CANVAS_AREA_MAX * 1.001;
  const reason = !isTruePreviewEdge(edge) ? `超过 ${limit} 上限`
    : areaLimited ? '受画布面积限制' : '真分辨率';
  return `实际约 1/${Number(ratio.toFixed(2))}（长边 ${edge}，${reason}）`;
}

/** Only authoritative same-scene dimensions can opt NOSR into a sharper tier. */
export function imagePreviewDiv(selected:number,kind:string|undefined,image:ImageSize,pan?:ImageSize) {
  if(kind!=='nosr'||!pan||![image.W,image.H,pan.W,pan.H].every(n=>Number.isFinite(n)&&n>0))return selected;
  if(image.W>pan.W||image.H>pan.H||(image.W===pan.W&&image.H===pan.H))return selected;
  return selected<=2?1:2;
}
