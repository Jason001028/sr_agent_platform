// 打印 fileDirectory 全部键名与 photometric 取值
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
const TIF = 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\u16_whitelzero.tif';
(async () => {
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));
    const input = await page.$('#fileInput');
    await input.uploadFile(TIF);
    await new Promise(r => setTimeout(r, 1500));
    const out = await page.evaluate(async () => {
        const file = window.recs[0].file;
        const t = await GeoTIFF.fromBlob(file);
        const img = await t.getImage();
        const fd = img.getFileDirectory();
        const keys = Object.keys(fd);
        return { keys: keys.slice(0, 100), json: JSON.stringify(fd).slice(0, 800) };
    });
    console.log(JSON.stringify(out, null, 2));
    await browser.close();
    process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
