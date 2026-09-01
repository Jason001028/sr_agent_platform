/**
 * saver.ts — 落盘层：文件系统访问 API + IndexedDB（tif-viewer.html 直译）
 * ------------------------------------------------------------------
 * 移植不重写：fsIO / downloadSaver / getSaver / openIDB / dateDirName / nextJpgName
 * 逐函数直译 HTML（1051-1189 行）；downloadBlob 直译 1579 行。可注入 saver 供 E2E。
 *
 * 与 Vue 的接线差异（唯一改动）：HTML 用全局 outHandle/outName + updateOutDirUI() 直接操作 DOM；
 * 本文件把「状态变更通知」抽象成 outDir 监听回调（Vue store 注册后同步到响应式状态），
 * 其余落盘逻辑逐字节保留。
 */

/* ---------------- 类型声明（FS Access API 的 TS 声明在 lib.dom.d.ts 已含） ---------------- */
declare global {
  interface Window {
    showDirectoryPicker?: (opts?: { mode?: 'read' | 'readwrite' }) => Promise<FileSystemDirectoryHandle>;
  }
}

/** queryPermission/requestPermission 是较新 API，lib.dom.d.ts 未必含 → 手工补齐（HTML 依赖它做权限三态） */
export interface FileSystemDirectoryHandleWithPerm {
  queryPermission?(opts?: { mode?: 'read' | 'readwrite' }): Promise<'granted' | 'prompt' | 'denied'>;
  requestPermission?(opts?: { mode?: 'read' | 'readwrite' }): Promise<'granted' | 'prompt' | 'denied'>;
}

type PermHandle = FileSystemDirectoryHandle & FileSystemDirectoryHandleWithPerm;

/** 输出目录状态（供 Vue store 同步到响应式 refs） */
export interface OutDirState {
  name: string;
  permission: 'prompt' | 'granted' | 'denied';
  ready: boolean;
}

const FSA = {
  get available(): boolean {
    return !!window.showDirectoryPicker;
  },
};

let outHandle: PermHandle | null = null;
let outName = '';
let saverOverride: Saver | null = null;
let outDirListener: ((s: OutDirState) => void) | null = null;

/** 注册输出目录状态变更监听（Vue store 用它同步 Toolbar 三态按钮）；null 清除 */
export function setOutDirListener(fn: ((s: OutDirState) => void) | null): void {
  outDirListener = fn;
}

/** 读取当前输出目录状态 */
export function getOutDirState(): OutDirState {
  return {
    name: outName,
    permission: fsIO.permission,
    ready: fsIO.isReady(),
  };
}

/** HTML updateOutDirUI() 的 Vue 等价：只通知监听方，渲染由 store/组件负责 */
function updateOutDirUI(): void {
  if (outDirListener) outDirListener(getOutDirState());
}

export function dateDirName(): string {
  const d = new Date();
  const p = (n: number): string => (n < 10 ? '0' : '') + n;
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate());
}

export function nextJpgName(name: string): string {
  const m = name.match(/^(.*?)(?:_(\d+))?\.jpg$/);
  const n = m && m[2] ? (+m[2] + 1) : 2;
  return (m ? m[1] : name) + '_' + n + '.jpg';
}

export function openIDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const rq = indexedDB.open('tif-viewer-export', 1);
    rq.onupgradeneeded = () => {
      const db = rq.result;
      if (!db.objectStoreNames.contains('settings')) db.createObjectStore('settings', { keyPath: 'k' });
    };
    rq.onsuccess = () => resolve(rq.result);
    rq.onerror = () => reject(rq.error);
  });
}

export interface Saver {
  isReady(): boolean;
  uniqueJpgName(dateDir: string, base: string): Promise<string>;
  writeJpg(dateDir: string, name: string, blob: Blob): Promise<void>;
  appendLog(dateDir: string, entry: unknown): Promise<void>;
}

export const fsIO = {
  permission: 'prompt' as 'prompt' | 'granted' | 'denied',
  _db: null as IDBDatabase | null,
  _logLock: Promise.resolve() as Promise<unknown>,

  init(): Promise<void> {
    return openIDB().then((db) => {
      fsIO._db = db;
      return new Promise<void>((res) => {
        const tx = db.transaction('settings', 'readonly');
        tx.objectStore('settings').get('outDirHandle').onsuccess = (e) => {
          const v = (e.target as IDBRequest).result as { handle?: FileSystemDirectoryHandle } | undefined;
          if (v && v.handle) {
            outHandle = v.handle;
            outName = outHandle.name || '';
            fsIO.applyPermission();
          } else {
            updateOutDirUI();
          }
          res();
        };
      });
    }).catch(() => { updateOutDirUI(); });
  },

  applyPermission(): Promise<void> | void {
    if (!outHandle || !outHandle.queryPermission) {
      fsIO.permission = 'granted';
      updateOutDirUI();
      return;
    }
    return outHandle.queryPermission({ mode: 'readwrite' }).then((st) => {
      if (st === 'granted') fsIO.permission = 'granted';
      else if (st === 'prompt') fsIO.permission = 'prompt';
      else fsIO.permission = 'denied';
      updateOutDirUI();
    }).catch(() => { fsIO.permission = 'prompt'; updateOutDirUI(); });
  },

  authorize(): Promise<void> {
    if (!FSA.available) {
      return Promise.reject(new Error('当前浏览器不支持文件系统访问 API（需 Edge/Chrome）'));
    }
    const persist = (h: PermHandle): Promise<void> => {
      outHandle = h;
      outName = h.name || '';
      fsIO.permission = 'granted';
      return openIDB().then((db) => {
        return new Promise<void>((res, rej) => {
          const tx = db.transaction('settings', 'readwrite');
          tx.objectStore('settings').put({ k: 'outDirHandle', handle: h });
          tx.oncomplete = () => res();
          tx.onerror = () => rej(tx.error);
        });
      }).then(() => { updateOutDirUI(); });
    };
    const pick = () => window.showDirectoryPicker!({ mode: 'readwrite' }).then((h) => persist(h as PermHandle));
    // 已存句柄且只是权限未授权 → 优先重新授权同一目录（避免再次选目录）
    if (outHandle && outHandle.requestPermission) {
      const h = outHandle;               // 闭包捕获：TS 在 if 内已缩小为非空且有 requestPermission
      return h.requestPermission!({ mode: 'readwrite' }).then(
        (st) => (st === 'granted' ? persist(h) : pick()),
        () => pick(),
      );
    }
    return pick();
  },

  isReady(): boolean {
    return !!outHandle && fsIO.permission === 'granted';
  },

  getDateDir(name: string): Promise<FileSystemDirectoryHandle> {
    return outHandle!.getDirectoryHandle(name, { create: true });
  },

  uniqueJpgName(dateDir: string, base: string): Promise<string> {
    return fsIO.getDateDir(dateDir).then((d) => {
      const tryName = (candidate: string): Promise<string> =>
        d.getFileHandle(candidate, { create: false })
          .then(() => tryName(nextJpgName(candidate)))   // 已存在 → 下一个
          .catch((e) => (e && e.name === 'NotFoundError' ? candidate : Promise.reject(e)));
      return tryName(base);
    });
  },

  writeJpg(dateDir: string, name: string, blob: Blob): Promise<void> {
    return fsIO.getDateDir(dateDir).then((d) =>
      d.getFileHandle(name, { create: true }).then((fh) =>
        fh.createWritable().then((w) => w.write(blob).then(() => w.close())),
      ),
    );
  },

  appendLog(dateDir: string, entry: unknown): Promise<void> {
    fsIO._logLock = fsIO._logLock.then(() => {    // 串行追加，避免并发覆盖
      return fsIO.getDateDir(dateDir).then((d) => {
        const txtP = d.getFileHandle('读取记录.json', { create: false })
          .then((fh) => fh.getFile().then((f) => f.text()))
          .catch(() => null);
        return txtP.then((txt) => {
          let log: { tool: string; version: number; entries: unknown[] } | null = null;
          if (txt) {
            try {
              const obj = JSON.parse(txt);
              if (obj && Array.isArray(obj.entries)) log = obj;
            } catch (e) { /* 沿用默认 */ }
          }
          if (!log) log = { tool: 'tif-viewer', version: 1, entries: [] };
          log.entries.push(entry);
          return d.getFileHandle('读取记录.json', { create: true }).then((fh) =>
            fh.createWritable().then((w) => w.write(JSON.stringify(log, null, 2)).then(() => w.close())),
          );
        });
      });
    });
    return fsIO._logLock as Promise<void>;
  },
};

// 降级方案：FS API 不可用（旧浏览器）→ 每次下载 jpg 到下载文件夹，不写日志。
export const downloadSaver: Saver = {
  isReady: () => true,
  uniqueJpgName: (dateDir: string, base: string) => Promise.resolve(dateDir + '_' + base),
  writeJpg: (dateDir: string, name: string, blob: Blob) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    return Promise.resolve();
  },
  appendLog: () => Promise.resolve(),
};

/** 取当前 saver：注入优先 → fsIO（已授权）→ FSA 不可用降级 downloadSaver → null（未授权） */
export function getSaver(): Saver | null {
  if (saverOverride) return saverOverride;
  if (fsIO.isReady()) return fsIO;
  if (!FSA.available) return downloadSaver;   // 浏览器不支持 FS API → 降级为下载
  return null;                                 // FS 可用但未授权 → kickExport 提示
}

/** E2E 注入假 saver */
export function setSaverOverride(s: Saver | null): void {
  saverOverride = s;
}

/** 浏览器下载 Blob（HTML downloadBlob 直译） */
export function downloadBlob(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}
