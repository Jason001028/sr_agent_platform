// lib/drag.js —— 拖放事件模拟（DragEvent + DataTransfer）
// ------------------------------------------------------------------
// 全仓第一个 DragEvent 模拟。既有脚本一律走 `input.uploadFile`（真·文件选择框那条路），
// 而查看器的**拖放**是另一条路径：监听挂在 window 上，处理函数只读 `clientX/clientY`
// 与 `e.dataTransfer.files`，从不读 `event.target`。所以这里：
//
//   - 事件派发到某个元素（默认画布）但 `bubbles: true` —— 冒泡到 window 才被处理函数看到；
//   - 坐标由调用方给**视口坐标**（与真实 drag 一致），不是元素内坐标；
//   - 文件在页面内用 `new File([bytes], name)` 现造。DataTransfer 是唯一能把 File 塞进
//     拖放事件的容器，浏览器不允许在事件对象上直接写 files。
//
// 两个分开的入口（**不能合并**）：`dropFiles` 发 dragover + drop；`dragOverOnly` 只发
// dragover。落位提示在 `onDrop` 里被同步清掉（TifCanvas.vue:348），要观察提示就必须在
// drop 之前单独问一次。
//
// **兜底**：某些 Chrome/Edge 版本用构造函数造出来的 `DataTransfer` 虽然在 `items` 里
// 看得到文件，`files` 却是空的；而页面只读 `e.dataTransfer.files`（TifCanvas.vue:349）。
// 长度不符时在实例上 `defineProperty('files', …)` 覆盖成同一个 FileList 语义的数组
// （页面只做 `Array.from(files)` 与 `.length`，数组足够）。
const fs = require('fs');
const path = require('path');

const DEFAULT_TARGET = 'canvas.view-canvas';

/** 页面内注入的小工具：按 base64 造 File，造 DataTransfer，必要时补 files。 */
function installHelper() {
  if (window.__dragKit) return;
  window.__dragKit = {
    b64ToBytes(b64) {
      const bin = atob(b64);
      const out = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out;
    },
    makeFiles(items) {
      return items.map((it) => new File([this.b64ToBytes(it.b64)], it.name, { type: '' }));
    },
    makeDT(files) {
      const dt = new DataTransfer();
      for (const f of files) dt.items.add(f);
      if (dt.files.length !== files.length) {
        Object.defineProperty(dt, 'files', { value: files, configurable: true });
      }
      return dt;
    },
    fire(el, type, dt, x, y) {
      const ev = new DragEvent(type, {
        dataTransfer: dt,
        clientX: x, clientY: y,
        bubbles: true, cancelable: true,
      });
      const notCancelled = el.dispatchEvent(ev);
      return { defaultPrevented: ev.defaultPrevented, notCancelled };
    },
  };
}

function readItems(files) {
  return files.map((f) => ({
    name: path.basename(f.path),
    b64: fs.readFileSync(f.path).toString('base64'),
  }));
}

/**
 * 只发 dragover（不发 drop）→ 用于观察落位提示。
 * @returns {Promise<{hint:{active:boolean,side:'A'|'B'|null}, defaultPrevented:boolean}>}
 *          hint 在事件派发**之后**同步读取（处理函数同步写 store），无需 sleep。
 */
async function dragOverOnly(page, opts) {
  const {
    files = [], clientX = 0, clientY = 0, target = DEFAULT_TARGET, times = 3,
  } = opts || {};
  const items = readItems(files);
  return page.evaluate((sel, its, x, y, n) => {
    if (!window.__dragKit) throw new Error('drag.js: 先 await installDragKit(page)');
    const el = document.querySelector(sel) || document.body;
    const dt = window.__dragKit.makeDT(window.__dragKit.makeFiles(its));
    let last = null;
    for (let i = 0; i < n; i++) last = window.__dragKit.fire(el, 'dragover', dt, x, y);
    return { hint: window.__viewer.dragHint(), defaultPrevented: last.defaultPrevented };
  }, target, items, clientX, clientY, times);
}

/**
 * 发 dragover ×3 + drop（真实拖放的顺序）→ 用于真正落图。
 * @returns {Promise<{defaultPrevented:boolean, before:number, after:number}>}
 *          before/after 是 recs 条数，省得调用方自己再问一次。
 */
async function dropFiles(page, opts) {
  const {
    files = [], clientX = 0, clientY = 0, target = DEFAULT_TARGET, times = 3,
  } = opts || {};
  const items = readItems(files);
  return page.evaluate((sel, its, x, y, n) => {
    if (!window.__dragKit) throw new Error('drag.js: 先 await installDragKit(page)');
    const el = document.querySelector(sel) || document.body;
    const dt = window.__dragKit.makeDT(window.__dragKit.makeFiles(its));
    const before = window.__viewer.recs().length;
    for (let i = 0; i < n; i++) window.__dragKit.fire(el, 'dragover', dt, x, y);
    const last = window.__dragKit.fire(el, 'drop', dt, x, y);
    return { defaultPrevented: last.defaultPrevented, before, after: window.__viewer.recs().length };
  }, target, items, clientX, clientY, times);
}

/** 装一次就够（页面上每次导航后都要重装）。 */
async function installDragKit(page) {
  await page.evaluate(installHelper);
}

/**
 * 发一次「页面内拖放」形态的 dragover：dataTransfer 里只有 text、**没有 Files**。
 * 在页面上拖一段选中的文字（或拖个链接）就是这一种 —— 浏览器照样发 dragover/drop，
 * 但 drop 时 `files` 是空的，什么都放不进来。用来验「落位提示只对真拖文件亮」：
 * 这种拖放一亮提示，用户就以为画面里拖一下要换格（2026-09-21 报的那个 bug）。
 * @returns {Promise<{hint:{active:boolean,side:'A'|'B'|null}, defaultPrevented:boolean, types:string[]}>}
 */
async function dragOverText(page, opts) {
  const {
    text = '一段被选中的文字', clientX = 0, clientY = 0, target = DEFAULT_TARGET,
  } = opts || {};
  return page.evaluate((sel, t, x, y) => {
    const el = document.querySelector(sel) || document.body;
    const dt = new DataTransfer();
    dt.setData('text/plain', t);
    const ev = new DragEvent('dragover', {
      dataTransfer: dt, clientX: x, clientY: y, bubbles: true, cancelable: true,
    });
    el.dispatchEvent(ev);
    return {
      hint: window.__viewer.dragHint(),
      defaultPrevented: ev.defaultPrevented,
      types: Array.from(dt.types || []),
    };
  }, target, text, clientX, clientY);
}

/**
 * 画布矩形的视口坐标 + 关键落点（左半心 / 右半心 / 画布外）。
 * 画布之外的点故意取画布左侧 40px（侧栏上方）—— 只要落在画布矩形外即可，
 * `dropSideAt` 判的是矩形包含，不看上面盖着什么元素。
 */
async function canvasPoints(page) {
  return page.evaluate(() => {
    const c = document.querySelector('canvas.view-canvas');
    const r = c.getBoundingClientRect();
    // 分隔线位置取 `splitX()`（= 左格宽，**画布局部**坐标）而不是自己拿比例乘一遍：
    // 落点判据 `paneAtX(localX, splitX)` 用的就是它，比例那侧是浮点、这侧已取整，
    // 自己乘出来的值会与处理器差不到 1px，正好压在分隔线上时判到另一格去。
    // 画布局部 x=0 与 `r.left` 对齐，所以这里直接把它当视口偏移用。
    const splitX = window.__viewer.splitX();
    return {
      rect: { left: r.left, top: r.top, width: r.width, height: r.height },
      midY: r.top + r.height / 2,
      left: r.left + splitX / 2,
      right: r.left + splitX + (r.width - splitX) / 2,
      outside: r.left - 40,
      outsideY: r.top + r.height / 2,
    };
  });
}

module.exports = {
  installDragKit, dragOverOnly, dragOverText, dropFiles, canvasPoints, DEFAULT_TARGET,
};
