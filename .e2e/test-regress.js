// 回归：大 16bit 无压缩文件（→稀疏条带路径，big_u16 全分辨率 8192²）+ 进度到 100 + 渲染正确；UTIF 分配失败自动回退仍在。
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
const BIG = 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\big_u16.tif';
const RGB = 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\rgb8.tif';

async function waitStatus(page, name, timeoutMs) {
    const dl = Date.now() + timeoutMs;
    while (Date.now() < dl) {
        const st = await page.evaluate((n) => {
            const el = [...document.querySelectorAll('.file-item')].find(x => x.querySelector('.name').textContent === n);
            return el ? el.querySelector('.status').textContent : null;
        }, name);
        if (st && (st.indexOf('完成') === 0 || st.indexOf('解码失败') === 0)) return st;
        await new Promise(r => setTimeout(r, 150));
    }
    return null;
}

(async () => {
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));

    // 1) 大 16bit 文件 → geotiff 分块，进度到 100%，缩略图正确
    const input = await page.$('#fileInput');
    await input.uploadFile(BIG);
    let peak = 0;
    const deadline = Date.now() + 120000;
    let st1 = null;
    while (Date.now() < deadline) {
        st1 = await waitStatus(page, 'big_u16.tif', 2000);
        const pct = await page.evaluate(() => parseInt(document.getElementById('progressPct').textContent || '0', 10));
        if (pct > peak) peak = pct;
        if (st1 && st1.indexOf('完成') === 0) break;
        await new Promise(r => setTimeout(r, 150));
    }
    const info1 = await page.evaluate(() => {
        const rec = window.activeRec;
        if (!rec || !rec.thumb) return { err: 'no thumb' };
        const c = rec.thumb, ctx = c.getContext('2d');
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        let sum = 0, n = c.width * c.height;
        const w = c.width, hl = Math.round(w * 0.2), hr = Math.round(w * 0.2);
        let left = 0, right = 0;
        for (let y = 0; y < c.height; y++) for (let x = 0; x < w; x++) {
            const i = (y * w + x) * 4; const v = (d[i] + d[i + 1] + d[i + 2]) / 3;
            sum += v;
            if (x < hl) left += v; else if (x > w - 1 - hr) right += v;
        }
        return { w: c.width, h: c.height, mean: +(sum / n).toFixed(1), leftMean: +(left / (hl * c.height)).toFixed(1), rightMean: +(right / (hr * c.height)).toFixed(1), W: rec.W, H: rec.H };
    });
    console.log('big_u16:', st1, '| peakProgress', peak, JSON.stringify(info1));
    const ok1 = st1 && st1.indexOf('读取耗时') !== -1 && peak === 100 && info1.W === 8192 && info1.H === 8192 &&
        info1.thumbW === undefined && info1.w === 8192 && info1.h === 8192 &&
        info1.mean > 50 && info1.mean < 205 && info1.leftMean < 100 && info1.rightMean > 155;

    await page.evaluate(() => { while (window.recs.length) window.removeFile(window.recs[0]); });

    // 2) UTIF.decodeImage 抛 RangeError → 仍自动回退 geotiff
    await page.evaluate(() => { window.UTIF.decodeImage = function () { throw new RangeError('Array buffer allocation failed'); }; });
    await input.uploadFile(RGB);
    const st2 = await waitStatus(page, 'rgb8.tif', 30000);
    console.log('fallback rgb8:', st2);
    const ok2 = st2 && st2.indexOf('读取耗时') !== -1;

    await browser.close();
    console.log('pageerrors:', JSON.stringify(errors));
    const pass = ok1 && ok2 && errors.length === 0;
    console.log(pass ? '\nALL PASS' : '\nFAIL');
    process.exit(pass ? 0 : 1);
})().catch(e => { console.error('TEST ERROR:', e); process.exit(1); });
