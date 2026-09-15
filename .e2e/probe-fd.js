// 探测 getFileDirectory() 的键名与 PhotometricInterpretation 取值
const puppeteer = require('puppeteer-core');
const EDGE = 'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe';
const PAGE_URL = 'file:///D:/BaiduNetdiskDownload/sr_agent_platform/tif_viewer/tif-viewer.html';
(async () => {
    const browser = await puppeteer.launch({ executablePath: EDGE, headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
    const page = await browser.newPage();
    await page.goto(PAGE_URL, { waitUntil: 'load' });
    await new Promise(r => setTimeout(r, 300));
    const input = await page.$('#fileInput');
    await input.uploadFile('D:\\BaiduNetdiskDownload\\sr_agent_platform\\test-tifs\\types\\u16_whitelzero.tif');
    await new Promise(r => setTimeout(r, 1500));
    const out = await page.evaluate(async () => {
        const file = window.recs[0].file;
        const t = await GeoTIFF.fromBlob(file);
        const img = await t.getImage();
        let fd = null, hasGfd = typeof img.getFileDirectory === 'function';
        if (hasGfd) fd = img.getFileDirectory();
        const keys = fd ? Object.keys(fd).filter(k => /[Pp]hot|Compress|Bits|Sample|Interpret/i.test(k)).slice(0, 30) : null;
        return {
            hasGfd,
            fdKeysSample: keys,
            photo: fd ? fd.PhotometricInterpretation : 'no-fd',
            photoLower: fd ? fd.photometricInterpretation : 'no-fd',
            compression: fd ? fd.Compression : 'no-fd',
            bits: fd ? fd.BitsPerSample : 'no-fd',
            spp: fd ? fd.SamplesPerPixel : 'no-fd',
            imgWidth: img.getWidth(),
            sf: img.getSampleFormat(),
        };
    });
    console.log(JSON.stringify(out, null, 2));
    await browser.close();
    process.exit(0);
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
