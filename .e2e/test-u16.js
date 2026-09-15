// 单独验证 u16_rgb.tif 走 geotiff 路径的缩略图像素
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
(async () => {
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));
    page.on('console', m => { if (m.text().indexOf('NORM') === 0) console.log(m.text()); });
    await page.evaluate(() => {
        window.SAFE = 0;
        const orig = window.normalizeRGBA;
        window.normalizeRGBA = function (raster, w, h, comps, bits) {
            let isU16 = raster instanceof Uint16Array, isU8 = raster instanceof Uint8Array;
            console.log('NORM w=' + w + ' h=' + h + ' comps=' + comps + ' bits=' + bits +
                ' ctor=' + raster.constructor.name + ' isU16=' + isU16 + ' isU8=' + isU8 +
                ' len=' + raster.length + ' first=' + JSON.stringify(Array.from(raster.slice(0, 6))));
            return orig(raster, w, h, comps, bits);
        };
    });
    const input = await page.$('#fileInput');
    await input.uploadFile('D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\u16_rgb.tif');
    const deadline = Date.now() + 30000;
    let status = null;
    while (Date.now() < deadline) {
        status = await page.evaluate(() => {
            const el = [...document.querySelectorAll('.file-item')].find(x => x.querySelector('.name').textContent === 'u16_rgb.tif');
            return el ? el.querySelector('.status').textContent : null;
        });
        if (status && status.indexOf('完成') === 0) break;
        await new Promise(r => setTimeout(r, 150));
    }
    const out = await page.evaluate(() => {
        const rec = window.activeRec;
        if (!rec || !rec.thumb) return { err: 'no thumb', status: document.querySelector('.file-item .status').textContent };
        const c = rec.thumb, ctx = c.getContext('2d');
        const d = ctx.getImageData(0, 0, c.width, c.height).data;
        let sum = 0, black = 0, n = c.width * c.height;
        const row0 = [];
        for (let x = 0; x < Math.min(8, c.width); x++) {
            const i = x * 4;
            row0.push([d[i], d[i + 1], d[i + 2]]);
        }
        for (let i = 0; i < d.length; i += 4) {
            const v = (d[i] + d[i + 1] + d[i + 2]) / 3;
            sum += v;
            if (v === 0) black++;
        }
        return { w: c.width, h: c.height, mean: (sum / n).toFixed(1), blackPct: (black / n * 100).toFixed(1), row0, status: document.querySelector('.file-item .status').textContent };
    });
    console.log(JSON.stringify(out, null, 2));
    await browser.close();
    process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
