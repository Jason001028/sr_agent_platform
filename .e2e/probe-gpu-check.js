// 一次性探查：headless Chrome 到底跑在真显卡还是 SwiftShader 上。
// 顺带做「读回是否让下一帧变贵」的对照（GPU 路径上才有意义）。
//   node probe-gpu-check.js [extraArg...]
const { launchPage } = require('./launchBrowser');

const EXTRA = process.argv.slice(2);

const FN = function () {
  const c = document.createElement('canvas');
  const gl = c.getContext('webgl') || c.getContext('experimental-webgl');
  const dbg = gl && gl.getExtension('WEBGL_debug_renderer_info');
  const renderer = dbg ? gl.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : (gl ? gl.getParameter(gl.RENDERER) : 'no-webgl');

  function makeSrc(n) {
    const x = document.createElement('canvas');
    x.width = n; x.height = n;
    const g = x.getContext('2d');
    g.fillStyle = '#123'; g.fillRect(0, 0, n, n);
    for (let i = 0; i < 2000; i++) {
      g.fillStyle = 'rgba(' + ((i * 37) % 255) + ',' + ((i * 91) % 255) + ',' + ((i * 13) % 255) + ',0.5)';
      g.fillRect((i * 613) % n, (i * 271) % n, 8, 8);
    }
    return x;
  }
  // 一帧：整张源图缩放着画上去（= 应用里的 render）
  function frame(dctx, src, scale) {
    dctx.clearRect(0, 0, 800, 600);
    dctx.imageSmoothingEnabled = scale < 4;
    dctx.drawImage(src, 0, 0, src.width * scale, src.height * scale);
  }
  function gesture(dctx, src, n, scale0) {
    return new Promise((res) => {
      const d = []; let i = 0, last = 0;
      (function loop(ts) {
        if (last) d.push(+(ts - last).toFixed(2));
        last = ts;
        frame(dctx, src, scale0 * Math.pow(1.2, i));
        if (++i >= n) return res(d);
        requestAnimationFrame(loop);
      })(0);
      requestAnimationFrame(function (ts) { last = ts; d.length = 0; });
    });
  }
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  return (async function () {
    const out = { renderer: renderer, results: {} };
    const N = 8192;
    const src = makeSrc(N);
    const dst = document.createElement('canvas');
    dst.width = 800; dst.height = 600;
    const dctx = dst.getContext('2d');
    const sctx = src.getContext('2d');
    const scale0 = Math.min(800 / N, 600 / N, 1);

    // 1) 干净源：两次手势（中间空闲），看第二段首帧是否变贵
    out.results.clean_gesture1 = await gesture(dctx, src, 6, scale0);
    await sleep(1500);
    out.results.clean_gesture2 = await gesture(dctx, src, 6, scale0);

    // 2) 手势之间对源画布做一次读回（= 应用里的 getImageData）
    await sleep(1500);
    sctx.getImageData(0, 0, 64, 64);
    out.results.after_readback = await gesture(dctx, src, 6, scale0);

    // 3) 再空闲一次，不读回
    await sleep(1500);
    out.results.after_idle2 = await gesture(dctx, src, 6, scale0);
    return out;
  })();
};

(async () => {
  const { browser, page } = await launchPage({ args: EXTRA });
  try {
    await page.goto('about:blank');
    console.log('附加参数:', EXTRA.length ? EXTRA.join(' ') : '(无)');
    console.log(JSON.stringify(await page.evaluate(FN), null, 2));
  } catch (e) {
    console.log('失败:', e.message);
  } finally {
    await browser.close();
  }
})();
