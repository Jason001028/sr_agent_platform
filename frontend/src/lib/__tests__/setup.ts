/**
 * Vitest 全局 setup（node 环境）
 * ------------------------------------------------------------------
 * geotiff 3.x 的 BlobSource（GeoTIFF.fromBlob 内部）用 `new FileReader()` + onloadend
 * 读取 Blob；Node 没有 FileReader，这里补一个最小 shim：readAsArrayBuffer 调
 * blob.arrayBuffer()，完成后把 result 挂到实例并触发 onload/onloadend（带 event.target，
 * 兼容 geotiff 读 event.target.result 的实现）。首次只触发 onloadend 会挂起无输出，
 * 必须同时触发 onload（geotiff 某些构建挂在 onload 上）。已由探针脚本验证 fromBlob
 * → getImage → getWidth/getSamplesPerPixel 全链路可用。
 */
interface ShimFileReader {
  result: ArrayBuffer | string | null;
  onload: ((ev?: unknown) => void) | null;
  onloadend: ((ev?: unknown) => void) | null;
  onerror: ((ev?: unknown) => void) | null;
  readAsArrayBuffer(blob: Blob): void;
}

if (!(globalThis as { FileReader?: unknown }).FileReader) {
  class FileReaderShim implements ShimFileReader {
    result: ArrayBuffer | string | null = null;
    onload: ((ev?: unknown) => void) | null = null;
    onloadend: ((ev?: unknown) => void) | null = null;
    onerror: ((ev?: unknown) => void) | null = null;

    readAsArrayBuffer(blob: Blob): void {
      blob
        .arrayBuffer()
        .then((buf) => {
          this.result = buf;
          const ev = { target: this };
          if (this.onload) this.onload(ev);
          if (this.onloadend) this.onloadend(ev);
        })
        .catch((err: unknown) => {
          if (this.onerror) this.onerror({ target: this, error: err });
        });
    }
  }
  (globalThis as { FileReader: unknown }).FileReader = FileReaderShim;
}
