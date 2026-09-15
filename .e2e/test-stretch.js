// 验证拉伸模式切换：线性→平方根→2%→对数→直方图均衡，画布内容随之变化且无报错
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
const F32 = 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\f32_skew.tif';

async function snap(page) {
    return page.evaluate(() => {
        const c = window.activeRec.thumb, ctx = c.getContext('2d');
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        let sum = 0, n = c.width * c.height, w = c.width;
        const hl = Math.round(w * 0.2), hr = Math.round(w * 0.2);
        let left = 0, right = 0;
        for (let y = 0; y < c.height; y++) for (let x = 0; x < w; x++) {
            const i = (y * w + x) * 4, v = (d[i] + d[i + 1] + d[i + 2]) / 3;
            sum += v;
            if (x < hl) left += v; else if (x > w - 1 - hr) right += v;
        }
        return { mean: +(sum / n).toFixed(1), leftMean: +(left / (hl * c.height)).toFixed(1), rightMean: +(right / (hr * c.height)).toFixed(1) };
    });
}

(async () => {
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));
    const input = await page.$('#fileInput');
    await input.uploadFile(F32);
    // 等解码完成
    for (let i = 0; i < 100; i++) {
        const st = await page.evaluate(() => window.activeRec && window.activeRec.thumb ? window.activeRec.status : '');
        if (st.indexOf('完成') === 0) break;
        await new Promise(r => setTimeout(r, 100));
    }
    const linear = await snap(page);
    const modes = { sqrt: '平方根', linear2: '2% 线性', log: '对数', equal: '直方图均衡' };
    const outs = { linear };
    for (const [val, label] of Object.entries(modes)) {
        await page.select('#stretchSel', val);
        await new Promise(r => setTimeout(r, 120));
        outs[val] = await snap(page);
        outs[val].label = label;
    }
    console.log(JSON.stringify(outs, null, 2));
    // 断言：所有模式渲染有效（非全黑/全白）；sqrt 与 equal 必须与线性显著不同（证明切换生效）
    let ok = true;
    for (const [k, o] of Object.entries(outs)) {
        if (!(o.mean > 20 && o.mean < 235)) { ok = false; console.log('FAIL mean out of range:', k, o.mean); }
    }
    for (const k of ['sqrt', 'equal']) {
        if (Math.abs(outs[k].leftMean - linear.leftMean) < 15) { ok = false; console.log('FAIL', k, 'did not change render:', outs[k].leftMean, 'vs', linear.leftMean); }
    }
    const changed = ['sqrt', 'linear2', 'log', 'equal'].filter(k => Math.abs(outs[k].mean - linear.mean) > 1);
    console.log('all modes render distinct (mean diff>1):', JSON.stringify(changed));
    if (changed.length < 4) { ok = false; console.log('FAIL: some mode rendered identical to another'); }
    if (errors.length) { ok = false; console.log('pageerrors:', JSON.stringify(errors)); }
    await browser.close();
    console.log(ok ? '\nALL PASS' : '\nFAIL');
    process.exit(ok ? 0 : 1);
})().catch(e => { console.error('TEST ERROR:', e); process.exit(1); });
