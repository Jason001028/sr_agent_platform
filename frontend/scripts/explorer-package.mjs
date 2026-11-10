import { readFileSync } from 'node:fs';

// A small, deterministic ZIP (stored entries, UTF-8 names, DOS date 1980-01-01).
// Generated from the actual sources on every build: no stale checked-in archive.
function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export function createExplorerSetupArchive() {
  const local = [], central = [];
  let offset = 0;
  const files = ['Install-SrExplorer.cmd', 'Install-SrExplorer.ps1', 'Chrome-SrExplorerPolicy.ps1', 'Pick-SrImage.ps1'];
  for (const name of files) {
    const text = readFileSync(new URL(`../public/client-explorer/${name}`, import.meta.url), 'utf8');
    const data = Buffer.from(text.replace(/\r?\n/g, '\r\n'));
    const filename = Buffer.from(name);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50, 0); header.writeUInt16LE(20, 4);
    header.writeUInt16LE(0x800, 6); header.writeUInt16LE(0x21, 12);
    header.writeUInt32LE(crc32(data), 14);
    header.writeUInt32LE(data.length, 18); header.writeUInt32LE(data.length, 22);
    header.writeUInt16LE(filename.length, 26);
    local.push(header, filename, data);
    const directory = Buffer.alloc(46);
    directory.writeUInt32LE(0x02014b50, 0);
    directory.writeUInt16LE(20, 4); directory.writeUInt16LE(20, 6);
    header.copy(directory, 8, 6, 28);
    directory.writeUInt32LE(offset, 42);
    central.push(directory, filename);
    offset += header.length + filename.length + data.length;
  }
  const index = Buffer.concat(central);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8); end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(index.length, 12); end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, index, end]);
}

export function explorerSetupPlugin() {
  const fileName = 'client-explorer/SrExplorer-Setup.zip';
  return {
    name: 'sr-explorer-setup',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName, source: createExplorerSetupArchive() });
    },
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (new URL(req.url, 'http://localhost').pathname !== '/' + fileName) return next();
        res.setHeader('Content-Type', 'application/zip');
        res.setHeader('Content-Disposition', 'attachment; filename="SrExplorer-Setup.zip"');
        res.end(createExplorerSetupArchive());
      });
    },
  };
}
