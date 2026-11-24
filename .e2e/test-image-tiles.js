// Start serve-image-open-api.py with a GDAL-enabled Python first.
const puppeteer = require('puppeteer-core');
const assert = require('node:assert/strict');

async function main() {
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH,
    headless: true,
  });
  try {
    const page = await browser.newPage();
    await page.goto('http://localhost:8766/fixture/stats');
    const result = await page.evaluate(async () => {
      const origin = 'http://127.0.0.1:8766';
      async function get(path, init) {
        const response = await fetch(origin + path, init);
        if (!response.ok) throw Error(`${path} ${response.status} ${await response.text()}`);
        return response;
      }
      const asset = await (await get('/api/image-open/assets', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: 'X:\\native-source.tif' }),
      })).json();
      const base = '/api/image-open/assets/' + asset.id;
      const response = await get(base + '/tiles/0/1/2');
      const tile = JSON.parse(response.headers.get('X-SR-Tile'));
      const bitmap = await createImageBitmap(await response.blob());
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(bitmap, 0, 0);
      bitmap.close();
      document.body.appendChild(canvas);
      const rgba = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      let min = 255, max = 0;
      for (let i = 0; i < rgba.length; i += 4) {
        if (rgba[i] !== rgba[i + 1] || rgba[i] !== rgba[i + 2] || rgba[i + 3] !== 255) {
          throw Error('Display tile must draw opaque grayscale');
        }
        min = Math.min(min, rgba[i]);
        max = Math.max(max, rgba[i]);
      }
      const raw = await get(base + '/raw-window?x=512&y=1024&width=512&height=512');
      const window = JSON.parse(raw.headers.get('X-SR-Raster-Window'));
      const dn = new DataView(await raw.arrayBuffer());
      for (let y = 0; y < 512; y++) {
        for (let x = 0; x < 512; x++) {
          const expected = ((1024 + y) * 2300 + 512 + x) & 65535;
          if (dn.getUint16(2 * (y * 512 + x), true) !== expected) throw Error('Original TIFF DN mismatch');
        }
      }
      const jpeg = await get(base + '/preview?div=2');
      const legacy = await createImageBitmap(await jpeg.blob());
      const previewSize = [legacy.width, legacy.height];
      legacy.close();
      return { size: [canvas.width, canvas.height], tile, window, previewSize, min, max };
    });
    assert.deepEqual(result.size, [512, 512]);
    assert.deepEqual(result.previewSize, [1150, 1150]);
    assert.deepEqual([result.tile.x, result.tile.y], [512, 1024]);
    assert.equal(result.tile.rasterVersion, result.window.rasterVersion);
    assert.equal(result.window.dtype, '<u2');
    assert.ok(result.max > result.min);
    console.log(await browser.version(), 'PNG drawn, 262144 native DN checked, legacy JPEG decoded');
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
