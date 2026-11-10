/**
 * 双数据源统一抽象（前端迁移决策：platform-tutorial.md 附录 A.3「前端实现约束」）
 * ---------------------------------------------------------------
 * 查看器数据源：本地 File + 盘阵 HTTP 两条并存。
 * 解码层全部逻辑只依赖 `source.read(offset, len) → Promise<ArrayBuffer>`，
 * 因此 IFD 探测 / 稀疏条带 / 分块 / 拉伸在两种数据源上完全复用，不写两套。
 *
 * - FileSource：本地 File/Blob（`blob.slice`，越界自动 clamp，与 HTML 版 `file.slice` 同语义）。
 * - HttpSource：盘阵 Range 读取。Phase 4 接入后端 strip/窗口读取端点后实现；
 *   Phase 1 只定契约，read 抛"未实现"以防误用（HttpSource 不应在未接端点前被调用）。
 */
export interface Source {
  /** 文件总字节数 */
  readonly size: number;
  /** 显示名（可选） */
  readonly name?: string;
  /** 读取 [offset, offset+length) 字节；越界时返回实际可得部分（与 Blob.slice 语义一致） */
  read(offset: number, length: number): Promise<ArrayBuffer>;
}

import type { LoadControl } from './loadControl';

export class FileSource implements Source {
  readonly size: number;
  readonly name?: string;
  readonly blob: Blob;

  constructor(blob: Blob, name?: string, private control?: LoadControl) {
    this.blob = blob;
    this.size = blob.size;
    this.name = name || (blob as { name?: string }).name;
  }

  async read(offset: number, length: number): Promise<ArrayBuffer> {
    await this.control?.checkpoint();
    const part = this.blob.slice(offset, offset + length);
    if (!this.control || typeof FileReader === 'undefined') {
      const pending = part.arrayBuffer();
      return this.control ? this.control.wait(pending) : pending;
    }
    const control = this.control;
    const buffer = await new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      const clean = () => control.signal.removeEventListener('abort', abort);
      const abort = () => { reader.abort(); clean(); reject(new DOMException('加载已停止', 'AbortError')); };
      reader.onload = () => { clean(); resolve(reader.result as ArrayBuffer); };
      reader.onerror = () => { clean(); reject(reader.error); };
      reader.onabort = () => { clean(); reject(new DOMException('加载已停止', 'AbortError')); };
      control.signal.addEventListener('abort', abort, { once: true });
      if (control.signal.aborted) abort(); else reader.readAsArrayBuffer(part);
    });
    await control.checkpoint();
    return buffer;
  }
}

export class HttpSource implements Source {
  readonly size: number;
  readonly name?: string;
  readonly url: string;

  constructor(url: string, size: number, name?: string) {
    this.url = url;
    this.size = size;
    this.name = name;
  }

  read(_offset: number, _length: number): Promise<ArrayBuffer> {
    return Promise.reject(
      new Error('HttpSource.read 尚未实现（Phase 4 接入后端 Range/strip 读取端点）'),
    );
  }
}

/** 从 Source 读前 n 字节（头部解析用；越界返回实际可得部分） */
export async function readHead(source: Source, length: number): Promise<Uint8Array> {
  const buf = await source.read(0, length);
  return new Uint8Array(buf);
}
