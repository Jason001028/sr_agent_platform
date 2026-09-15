// 探测 geotiff.js readRasters 对各类数据返回的 Array 类型与值域
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
const FILES = [
    ['rgb8.tif', 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\rgb8.tif'],
    ['u16_rgb.tif', 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\u16_rgb.tif'],
    ['u32_gray.tif', 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\u32_gray.tif'],
    ['f32_gray.tif', 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\f32_gray.tif'],
    ['f32_reflect.tif', 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\f32_reflect.tif'],
    ['f32_rgb.tif', 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\f32_rgb.tif'],
    ['gray16.tif', 'D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\gray16.tif'],
];
(async () => {
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));
    for (const [name, p] of FILES) {
        const input = await page.$('#fileInput');
        await input.uploadFile(p);
        const info = await page.evaluate(async () => {
            const file = window.recs.length ? window.recs[window.recs.length - 1].file : null;
            if (!file) return { err: 'no rec file' };
            try {
                const tiff = await GeoTIFF.fromBlob(file);
                const img = await tiff.getImage();
                const W = img.getWidth(), H = img.getHeight();
                const spp = img.getSamplesPerPixel();
                const samples = spp >= 3 ? [0, 1, 2] : [0];
                const r = await img.readRasters({ window: [0, 0, Math.min(64, W), Math.min(64, H)], samples: samples, interleave: true });
                if (r == null) return { err: 'readRasters returned null' };
                let min = Infinity, max = -Infinity, nanCount = 0;
                for (let i = 0; i < r.length; i++) {
                    const v = r[i];
                    if (v !== v) { nanCount++; continue; }
                    if (v < min) min = v; if (v > max) max = v;
                }
                const hasSF = typeof img.getSampleFormat === 'function';
                return {
                    ctor: r.constructor.name, len: r.length,
                    first8: Array.from(r.slice(0, 8)),
                    min, max, nanCount,
                    bits: img.getBitsPerSample(), spp: img.getSamplesPerPixel(),
                    hasSampleFormat: hasSF, sampleFormat: hasSF ? img.getSampleFormat() : 'n/a',
                    w: W, h: H,
                };
            } catch (e) {
                return { err: e.message };
            }
        });
        console.log(name, '=>', JSON.stringify(info));
    }
    await browser.close();
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
