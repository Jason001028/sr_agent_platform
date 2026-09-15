// 直接测页面里的 normalizeRGBA，隔离 chunkedPreview
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
(async () => {
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));
    const out = await page.evaluate(() => {
        const src = new Uint16Array([0, 65535, 0, 257, 65278, 0]); // 2px RGB: 纯绿 + 亮青
        const res = window.normalizeRGBA(src, 2, 1, 3, 16);
        return { out: Array.from(res), srcType: src.constructor.name };
    });
    console.log(JSON.stringify(out));
    await browser.close();
    process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
