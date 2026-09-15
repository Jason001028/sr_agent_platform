// 测试 getField/getTag 取 photometric/compression
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
        const tryKey = (k) => {
            try { const v = img.getField(k); return { k, v: v !== undefined ? v : '<undefined>' }; }
            catch (e) { return { k, err: e.message }; }
        };
        const tryKey2 = (k) => {
            try { const v = img.getTag(k); return { k, v: v !== undefined ? v : '<undefined>' }; }
            catch (e) { return { k, err: e.message }; }
        };
        const names = ['PhotometricInterpretation', 'photometricInterpretation', 'Compression', 'BitsPerSample', 'SamplesPerPixel', 'SampleFormat', 'ImageWidth'];
        return {
            hasGetField: typeof img.getField === 'function',
            hasGetTag: typeof img.getTag === 'function',
            fields: names.map(tryKey),
            tags: names.map(tryKey2),
            n3: (() => { try { return img.getField(262); } catch (e) { return 'err:' + e.message; } })(),
        };
    });
    console.log(JSON.stringify(out, null, 2));
    await browser.close();
    process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
