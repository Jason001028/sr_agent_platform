// 8bit UTIF 路径：线性=原样恒等；切平方根应增强；paintedMode 正确跟随
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
const RGB = 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\rgb8.tif';
async function snap(page) {
    return page.evaluate(() => {
        const c = window.activeRec.thumb, ctx = c.getContext('2d');
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        let sum = 0, n = c.width * c.height, w = c.width;
        const hl = Math.round(w * 0.2); let left = 0;
        for (let y = 0; y < c.height; y++) for (let x = 0; x < w; x++) {
            const i = (y * w + x) * 4, v = (d[i] + d[i + 1] + d[i + 2]) / 3;
            sum += v; if (x < hl) left += v;
        }
        return { mean: +(sum / n).toFixed(1), leftMean: +(left / (hl * c.height)).toFixed(1), painted: window.activeRec.paintedMode };
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
    await input.uploadFile(RGB);
    for (let i = 0; i < 80; i++) {
        const st = await page.evaluate(() => window.activeRec ? window.activeRec.status : '');
        if (st.indexOf('完成') === 0) break;
        await new Promise(r => setTimeout(r, 100));
    }
    const linear = await snap(page);
    await page.select('#stretchSel', 'sqrt'); await new Promise(r => setTimeout(r, 150));
    const sqrt = await snap(page);
    await page.select('#stretchSel', 'equal'); await new Promise(r => setTimeout(r, 150));
    const equal = await snap(page);
    console.log('linear', JSON.stringify(linear));
    console.log('sqrt  ', JSON.stringify(sqrt));
    console.log('equal ', JSON.stringify(equal));
    const ok = linear.painted === 'linear' && sqrt.painted === 'sqrt' && equal.painted === 'equal'
        && sqrt.mean > linear.mean && Math.abs(equal.leftMean - linear.leftMean) > 1 && errors.length === 0;
    await browser.close();
    console.log(ok ? '\nALL PASS' : '\nFAIL');
    process.exit(ok ? 0 : 1);
})().catch(e => { console.error('ERR', e); process.exit(1); });
