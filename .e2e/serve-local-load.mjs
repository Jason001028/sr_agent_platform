// Build like production: local UMD TIFF vendors require Rollup's CJS adapter.
import path from 'node:path';
import fs from 'node:fs';
import http from 'node:http';
import { deflateSync } from 'node:zlib';
import { fileURLToPath, pathToFileURL } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const { build } = await import(pathToFileURL(path.join(root, 'frontend/node_modules/vite/dist/node/index.js')).href);
const outDir = path.join(root, '.e2e/fixtures/local-load-build');
await build({ root: path.join(root, 'frontend'), configFile: path.join(root, 'frontend/vite.config.ts'), base: '/',
  build: { outDir, rollupOptions: { input: [path.join(root, 'frontend/tests/local-load.html'),path.join(root,'frontend/tests/image-open.html')] } } });
// Real compressed 16-bit TIFF for the non-sparse worker route, no native tools.
{
  const W = 2049, H = 2049, rows = 64, count = Math.ceil(H/rows), parts = [];
  for (let y = 0; y < H; y += rows) {
    const raw = Buffer.alloc(W*Math.min(rows,H-y)*2);
    for (let i = 0; i < raw.length/2; i++) raw.writeUInt16LE(((y+Math.floor(i/W))*7+(i%W)*3)&65535,i*2);
    parts.push(deflateSync(raw));
  }
  const offsets = 256, lengths = offsets+count*4, header = Buffer.alloc(lengths+count*4);
  header.write('II'); header.writeUInt16LE(42,2); header.writeUInt32LE(8,4); header.writeUInt16LE(10,8);
  const tags = [[256,4,1,W],[257,4,1,H],[258,3,1,16],[259,3,1,8],[262,3,1,1],
    [273,4,count,offsets],[277,3,1,1],[278,4,1,rows],[279,4,count,lengths],[339,3,1,1]];
  tags.forEach(([tag,type,n,value],i)=>{const at=10+i*12;header.writeUInt16LE(tag,at);header.writeUInt16LE(type,at+2);header.writeUInt32LE(n,at+4);header.writeUInt32LE(value,at+8);});
  let position = header.length;
  parts.forEach((part,i)=>{header.writeUInt32LE(position,offsets+i*4);header.writeUInt32LE(part.length,lengths+i*4);position+=part.length;});
  fs.writeFileSync(path.join(outDir,'tests/deflate-rows.tif'),Buffer.concat([header,...parts]));
}
const server = http.createServer((req, res) => {
  const file = path.resolve(outDir, '.' + new URL(req.url, 'http://localhost').pathname);
  if (!file.startsWith(outDir + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    res.writeHead(404).end(); return;
  }
  const type = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript', '.css': 'text/css', '.png': 'image/png', '.webp': 'image/webp' }[path.extname(file)] || 'application/octet-stream';
  res.writeHead(200, { 'Content-Type': type }); fs.createReadStream(file).pipe(res);
});
server.listen(5179, '127.0.0.1', () => console.log('Local loading fixture: http://127.0.0.1:5179/tests/local-load.html'));
