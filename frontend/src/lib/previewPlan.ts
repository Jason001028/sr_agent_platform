export interface PreviewSource {
  width: number;
  height: number;
  identity: string;
  version: string | null;
  bandCount?: number;
  dtype?: string;
  noData?: number | string;
  serverWindowRead: boolean;
  localFile: boolean;
}

export interface PreviewPlan {
  source: PreviewSource;
  requestedDiv: number;
  width: number;
  height: number;
  scaleX: number;
  scaleY: number;
  pixels: number;
  decodedBytes: number; // One decoded RGBA surface, in bytes.
  dnBytes?: number;
  artifactId?: string;
  mode: 'whole-jpeg' | 'local-progressive' | 'tiles';
  limits: ('long-edge' | 'area' | 'memory' | 'reader')[];
  cache?: 'hit' | 'cached' | 'generated' | 'production-reuse';
}

// Keep Blob callers and the existing in-memory cache API intact.
export const jpegPlans = new WeakMap<Blob, PreviewPlan>();

export async function previewResponseBlob(response: Response): Promise<Blob> {
  const blob = await response.blob();
  const header = response.headers.get('X-SR-Preview-Plan');
  if (header) {
    try {
      const plan = JSON.parse(header) as PreviewPlan;
      if (plan.mode === 'whole-jpeg' && plan.source?.width > 0 && plan.source.height > 0
        && Number.isInteger(plan.width) && plan.width > 0 && Number.isInteger(plan.height) && plan.height > 0) {
        jpegPlans.set(blob, plan);
      }
    } catch { /* Older servers and legacy JPEGs use the JPEG frame header. */ }
  }
  return blob;
}
