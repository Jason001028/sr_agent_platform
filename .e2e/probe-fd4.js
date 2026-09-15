// 尝试 fd.fileDirectory.<Tag> 与 fd.actualizedFields 等访问路径
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
        const probe = (obj, label) => {
            if (!obj || typeof obj !== 'object') return { label, type: typeof obj };
            const names = ['PhotometricInterpretation', 'Compression', 'BitsPerSample', 'SamplesPerPixel', 'ImageWidth', 'SampleFormat'];
            const got = {};
            for (const n of names) {
                try { got[n] = obj[n]; } catch (e) { got[n] = 'ERR:' + e.message; }
            }
            const keys = Object.keys(obj);
            return { label, isArray: Array.isArray(obj), keys: keys.slice(0, 12), got };
        };
        return [
            probe(fd, 'fd'),
            probe(fd.fileDirectory, 'fd.fileDirectory'),
            probe(fd.actualizedFields, 'fd.actualizedFields'),
            probe(fd.deferredFields, 'fd.deferredFields'),
        ];
    });
    console.log(JSON.stringify(out, null, 2));
    await browser.close();
    process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
