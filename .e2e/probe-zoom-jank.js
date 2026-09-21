// 一次性探查（非回归）：大图缩放「开头卡一下、随后顺」的定性实验。
// 只测 drawImage 原语：把 src 画布按连续变化的 scale 缩放着画到 dest 上，
// 用 rAF 间隔当帧时（真实上屏节奏），并对比「手势之间空闲 1.5s」是否让下一帧重新变贵。
//   node probe-zoom-jank.js            默认：沿用 launchBrowser 的 --disable-gpu
//   node probe-zoom-jank.js --gpu      允许 GPU（看加速路径下形状是否一样）
const { launchPage } = require('./launchBrowser');

const USE_GPU = process.argv.includes('--gpu');

const PAGE_FN = function () {
  function makeSrc(n) {
    const c = document.createElement('canvas');
    c.width = n; c.height = n;
    const x = c.getContext('2d');
    // 内容无所谓（只测采样代价），但要有高频细节，避免被当成常量图走捷径
    const g = x.createLinearGradient(0, 0, n, n);
    g.addColorStop(0, '#102030'); g.addColorStop(1, '#e0d0c0');
    x.fillStyle = g; x.fillRect(0, 0, n, n);
    for (let i = 0; i < 4000; i++) {
      x.fillStyle = 'rgba(' + ((i * 37) % 255) + ',' + ((i * 91) % 255) + ',' + ((i * 13) % 255) + ',0.5)';
      x.fillRect((i * 613) % n, (i * 271) % n, (i % 17) + 1, (i % 13) + 1);
    }
    return c;
  }
  // 金字塔：一次预烤出各级（= 用户说的「预先加载缓存」），每级边长减半
  function makeLevels(src, n, min) {
    const lv = [src];
    let cur = src, size = n;
    while (size > min) {
      const ns = Math.max(min, size >> 1);
      const c = document.createElement('canvas');
      c.width = ns; c.height = ns;
      const x = c.getContext('2d');
      x.imageSmoothingEnabled = true;
      x.drawImage(cur, 0, 0, ns, ns);
      lv.push(c); cur = c; size = ns;
    }
    return lv;
  }
  function pickLevel(levels, firstSize, scale) {
    // 选「分辨率 ≥ 屏幕上实际占的像素」的最小一级 → 残余 scale 落在 [0.5, 1]
    const need = firstSize * scale;
    for (let i = 0; i < levels.length; i++) {
      if (levels[i].width >= need) {
        return { img: levels[i], s: need / levels[i].width };
      }
    }
    const last = levels[levels.length - 1];
    return { img: last, s: need / last.width };
  }

  const DEST_W = 1200, DEST_H = 800, N = 8192, STEPS = 40, IDLE_MS = 1500, ROUNDS = 2;

  function canvas() {
    const c = document.createElement('canvas');
    c.width = DEST_W; c.height = DEST_H;
    return c;
  }
  function drawFrame(ctx, img, scale) {
    const t0 = performance.now();
    ctx.clearRect(0, 0, DEST_W, DEST_H);
    ctx.imageSmoothingEnabled = scale < 4;
    ctx.drawImage(img, 0, 0, img.width * scale, img.height * scale);
    return performance.now() - t0;
  }

  // 用 rAF 驱动：每帧画一次（对应真实滚轮事件驱动重绘），记录帧间隔
  function runGesture(ctx, pick) {
    return new Promise((resolve) => {
      const scale0 = Math.min(DEST_W / N, DEST_H / N, 1);
      const deltas = [], sync = [];
      let i = 0, last = 0;
      function frame(ts) {
        if (last) deltas.push(ts - last);
        last = ts;
        const scale = scale0 * Math.pow(1.2, i);
        const { img, s } = pick(scale);
        sync.push(drawFrame(ctx, img, s));
        if (++i >= STEPS) return resolve({ deltas, sync });
        requestAnimationFrame(frame);
      }
      requestAnimationFrame(frame);
    });
  }

  function idle(ms) { return new Promise((r) => setTimeout(r, ms)); }
  function med(a) { const b = a.slice().sort((x, y) => x - y); return b[b.length >> 1]; }
  function round(a, k) { return a.map((v) => +v.toFixed(k === undefined ? 2 : k)); }

  return (async function () {
    const src = makeSrc(N);
    let t0 = performance.now();
    const levels = makeLevels(src, N, 512);
    const bakeMs = performance.now() - t0;
    const lvSizes = levels.map((c) => c.width).join('>');

    const out = { src: N + 'x' + N, dest: DEST_W + 'x' + DEST_H, bakeMs: +bakeMs.toFixed(1), levels: lvSizes };

    // A：现状 —— 直接画整张源图
    const ca = canvas(), actx = ca.getContext('2d');
    const a1 = await runGesture(actx, (scale) => ({ img: src, s: scale }));
    await idle(IDLE_MS);
    const a2 = await runGesture(actx, (scale) => ({ img: src, s: scale }));
    out.A_raw = {
      firstGesture: round(a1.deltas.slice(0, 6)),
      firstGestureSync: round(a1.sync.slice(0, 6)),
      afterIdle: round(a2.deltas.slice(0, 6)),
      afterIdleSync: round(a2.sync.slice(0, 6)),
      steadyMedian: +med(a1.deltas.slice(5)).toFixed(2),
      syncMedian: +med(a1.sync.slice(5)).toFixed(2),
    };

    // B：金字塔 —— 选级绘制（残余 scale 恒在 [0.5,1]）
    const cb = canvas(), bctx = cb.getContext('2d');
    const pick = (scale) => pickLevel(levels, N, scale);
    const b1 = await runGesture(bctx, pick);
    await idle(IDLE_MS);
    const b2 = await runGesture(bctx, pick);
    out.B_pyramid = {
      firstGesture: round(b1.deltas.slice(0, 6)),
      firstGestureSync: round(b1.sync.slice(0, 6)),
      afterIdle: round(b2.deltas.slice(0, 6)),
      afterIdleSync: round(b2.sync.slice(0, 6)),
      steadyMedian: +med(b1.deltas.slice(5)).toFixed(2),
      syncMedian: +med(b1.sync.slice(5)).toFixed(2),
    };

    function runGesture2(ctx, img, n) {
      return new Promise((resolve) => {
        const scale0 = Math.min(DEST_W / n, DEST_H / n, 1);
        const deltas = []; let i = 0, last = 0;
        function frame(ts) {
          if (last) deltas.push(ts - last);
          last = ts;
          drawFrame(ctx, img, scale0 * Math.pow(1.2, i));
          if (++i >= STEPS) return resolve({ deltas });
          requestAnimationFrame(frame);
        }
        requestAnimationFrame(frame);
      });
    }
    // C：小源图对照（2048，= 本地 TIF 那条路 buildThumb 的上限）
    const small = makeSrc(2048);
    const cc = canvas(), cctx = cc.getContext('2d');
    const c1 = await runGesture2(cctx, small, 2048);
    out.C_small2048 = {
      firstGesture: round(c1.deltas.slice(0, 6)),
      steadyMedian: +med(c1.deltas.slice(5)).toFixed(2),
    };
    return out;
  })();
};

(async () => {
  const { browser, page, errors } = await launchPage({
    args: USE_GPU ? ['--enable-gpu', '--use-angle=d3d11'] : [],
    headless: true,
  });
  try {
    await page.goto('about:blank');
    const res = await page.evaluate(PAGE_FN);
    console.log('GPU:', USE_GPU, '  视口:', await page.evaluate(() => innerWidth + 'x' + innerHeight));
    console.log(JSON.stringify(res, null, 2));
    if (errors.length) console.log('页面错误:', errors);
  } catch (e) {
    console.log('探查失败:', e.message);
  } finally {
    await browser.close();
  }
})();
