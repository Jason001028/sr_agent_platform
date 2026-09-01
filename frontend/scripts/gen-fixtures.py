#!/usr/bin/env python3
"""gen-fixtures.py — 零依赖自包含测试图生成（frontend/fixtures/）
------------------------------------------------------------------------
struct 手写 classic TIFF / BigTIFF（小端），供 Node Vitest golden 测试与
浏览器 .e2e 回归共用。与 test-tifs/（gitignore 的内网盘阵真实图）不同：
本目录文件小而自包含、入库随仓库分发。

生成产物：
  gray16_grad.tif     256×256 uint16 无压缩 RPS=8   v = col*257（稀疏 golden）
  rgb8.tif            64×64    uint8 RGB 无压缩 RPS=64  r=x,g=y,b=(x+y)&255
  u16_whitezero.tif   128×128 uint16 无压缩 RPS=128 photometric=0 v=(255-col)*257
  f32_grad.tif        128×128 float32 无压缩 RPS=128 v = col
  bigtiff_strips.tif  256×256 uint8 无压缩 RPS=16 BigTIFF(magic 43) v = col&255
  u16_rows8.tif       128×64  uint16 无压缩 RPS=8   v = col*257（非方形）
  manifest.json       每个 fixture 的布局元数据（测试断言用）

无第三方依赖（仅标准库）。重跑幂等：覆盖 fixtures/ 下同名文件。
"""
import json
import math
import os
import struct

OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'fixtures'))

TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 13: 4}


def align(off, n=2):
    return (off + n - 1) // n * n


def enc_vals(type_id, vals):
    """把标量列表编码为小端字节（type_id 仅支持本脚本用到的 1/3/4/11）"""
    if type_id == 1:
        return bytes(int(v) & 0xFF for v in vals)
    if type_id == 3:
        return struct.pack('<%dH' % len(vals), *(int(v) & 0xFFFF for v in vals))
    if type_id == 4:
        return struct.pack('<%dI' % len(vals), *(int(v) & 0xFFFFFFFF for v in vals))
    if type_id == 11:
        return struct.pack('<%df' % len(vals), *(float(v) for v in vals))
    raise ValueError('unsupported type %d' % type_id)


def write_tiff(path, W, H, spp, bits, photometric, compression, rps,
               pixel_fn, sample_format=1, big=False):
    """写一个（无压缩条带）TIFF。pixel_fn(x, y) → list[spp] 各样本值。"""
    bpp = bits // 8
    row_bytes = W * spp * bpp
    n_strips = math.ceil(H / rps)

    # 逐条带逐像素编码像素数据
    strip_data = []
    for i in range(n_strips):
        y0 = i * rps
        y1 = min(H, y0 + rps)
        buf = bytearray()
        for y in range(y0, y1):
            for x in range(W):
                for v in pixel_fn(x, y):
                    if bits == 8:
                        buf.append(int(v) & 0xFF)
                    elif bits == 16:
                        buf += struct.pack('<H', int(v) & 0xFFFF)
                    elif bits == 32 and sample_format == 3:
                        buf += struct.pack('<f', float(v))
                    else:
                        buf += struct.pack('<I', int(v) & 0xFFFFFFFF)
        strip_data.append(bytes(buf))
    strip_bytes = [len(d) for d in strip_data]

    vfield = 8 if big else 4
    inline_limit = vfield
    # 条目按 tag 升序排列（273/279 的 vals 为占位，布局定稿后回填）
    entries = [
        (256, 4, [W]),                    # ImageWidth
        (257, 4, [H]),                    # ImageLength
        (258, 3, [bits] * spp),           # BitsPerSample
        (259, 3, [compression]),          # Compression
        (262, 3, [photometric]),          # PhotometricInterpretation
        (273, 4, None),                   # StripOffsets（回填）
        (277, 3, [spp]),                  # SamplesPerPixel
        (278, 4, [rps]),                  # RowsPerStrip
        (279, 4, None),                   # StripByteCounts（回填）
        (284, 3, [1]),                    # PlanarConfiguration = chunky
        (339, 3, [sample_format] * spp),  # SampleFormat
    ]
    n = len(entries)

    # 已知每个条目的数据大小（273/279 为 4*n_strips 字节数组）
    data_sizes = [
        (4 * n_strips if vals is None else len(enc_vals(t, vals)))
        for (_, t, vals) in entries
    ]

    if big:
        ifd_off = 16
        ifd_size = 8 + n * 20 + 8
    else:
        ifd_off = 8
        ifd_size = 2 + n * 12 + 4

    # 布局：IFD → 超长值数组 → 条带数据（各按 2/8 字节对齐）
    align_n = 8 if big else 2
    cursor = ifd_off + ifd_size
    out_off = []
    for sz in data_sizes:
        if sz <= inline_limit:
            out_off.append(None)
        else:
            cursor = align(cursor, align_n)
            out_off.append(cursor)
            cursor += sz
    strip_offsets = []
    for d in strip_data:
        cursor = align(cursor, align_n)
        strip_offsets.append(cursor)
        cursor += len(d)
    total = cursor

    out = bytearray(total)

    def put_u16(o, v):
        struct.pack_into('<H', out, o, v & 0xFFFF)

    def put_u32(o, v):
        struct.pack_into('<I', out, o, v & 0xFFFFFFFF)

    def put_u64(o, v):
        struct.pack_into('<Q', out, o, v & 0xFFFFFFFFFFFFFFFF)

    def fill_entries(o):
        for i, (tag, t, _vals) in enumerate(entries):
            vals = strip_offsets if tag == 273 else strip_bytes if tag == 279 else _vals
            data = enc_vals(t, vals)
            put_u16(o, tag)
            put_u16(o + 2, t)
            if big:
                put_u64(o + 4, len(vals))
                if out_off[i] is not None:
                    put_u64(o + 12, out_off[i])
                else:
                    out[o + 12:o + 12 + len(data)] = data
                o += 20
            else:
                put_u32(o + 4, len(vals))
                if out_off[i] is not None:
                    put_u32(o + 8, out_off[i])
                else:
                    out[o + 8:o + 8 + len(data)] = data
                o += 12
        if big:
            put_u64(o, 0)  # 下一 IFD = 无
            o += 8
        else:
            put_u32(o, 0)
            o += 4
        return o

    if big:
        out[0:2] = b'II'
        put_u16(2, 43)      # BigTIFF magic
        put_u16(4, 8)       # offset size
        put_u16(6, 0)       # reserved
        put_u64(8, ifd_off)
        o = ifd_off
        put_u64(o, n)
        o += 8
        o = fill_entries(o)
    else:
        out[0:2] = b'II'
        put_u16(2, 42)      # classic magic
        put_u32(4, ifd_off)
        o = ifd_off
        put_u16(o, n)
        o += 2
        o = fill_entries(o)

    # 超长值数组
    for i, (tag, t, _vals) in enumerate(entries):
        if out_off[i] is None:
            continue
        vals = strip_offsets if tag == 273 else strip_bytes if tag == 279 else _vals
        data = enc_vals(t, vals)
        out[out_off[i]:out_off[i] + len(data)] = data
    # 条带数据
    for d, off in zip(strip_data, strip_offsets):
        out[off:off + len(d)] = d

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(out)
    return total


def build_all():
    os.makedirs(OUT, exist_ok=True)
    manifest = []

    def add(name, W, H, spp, bits, photometric, compression, rps,
            pixel_fn, sample_format=1, big=False):
        path = os.path.join(OUT, name)
        size = write_tiff(path, W, H, spp, bits, photometric, compression,
                          rps, pixel_fn, sample_format=sample_format, big=big)
        manifest.append({
            'name': name, 'bytes': size,
            'W': W, 'H': H, 'spp': spp, 'bits': bits,
            'photometric': photometric, 'compression': compression,
            'rps': rps, 'sample_format': sample_format, 'big': big,
        })

    # 稀疏 golden 图：uint16 渐变，v = col*257 ⇒ (v - min)/(max-min) = col/255 精确
    add('gray16_grad.tif', 256, 256, 1, 16, 1, 1, 8,
        lambda x, y: [x * 257], sample_format=1)

    # 小 8bit RGB（UTIF 快速路径）；r=x, g=y, b=(x+y)&255
    add('rgb8.tif', 64, 64, 3, 8, 2, 1, 64,
        lambda x, y: [x, y, (x + y) & 255], sample_format=1)

    # WhiteIsZero（photometric=0）→ 反相路径；256×128 且 v=(255-col)*257 覆盖全
    # [0,65535] 自然值 ⇒ 反相后线性显示 = col 可逐像素精确 golden
    add('u16_whitezero.tif', 256, 128, 1, 16, 0, 1, 128,
        lambda x, y: [(255 - x) * 257], sample_format=1)

    # float32 渐变（sample_format=3）；v = col
    add('f32_grad.tif', 128, 128, 1, 32, 1, 1, 128,
        lambda x, y: [float(x)], sample_format=3)

    # BigTIFF（magic 43）+ 多条带；v = col&255
    add('bigtiff_strips.tif', 256, 256, 1, 8, 1, 1, 16,
        lambda x, y: [x & 255], sample_format=1, big=True)

    # 非方形 16bit 多条带；v = col*257
    add('u16_rows8.tif', 128, 64, 1, 16, 1, 1, 8,
        lambda x, y: [x * 257], sample_format=1)

    with open(os.path.join(OUT, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print('wrote %d fixtures to %s' % (len(manifest), OUT))


if __name__ == '__main__':
    build_all()
