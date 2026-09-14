#!/usr/bin/env python3
"""生成 .e2e 回归测试所需测试图（纯标准库，零第三方依赖）。

设计目标：big_u16.tif —— 无压缩 + 条带1行 + 单波段 16bit classic TIFF，
解码字节 > 100MB，命中 tif-viewer 的「稀疏条带预览」路径（isSparseCandidate）。
像素为水平渐变 x/(N-1)*65535（左暗右亮），BlackIsZero（不反色）。

若本机有 tifffile 也可用它生成，但此脚本保持零依赖，内网机无镜像也能跑。

用法: python gen-test-images.py [输出目录]
"""
import struct
import os
import sys


def write_big_u16(path, n=8192):
    """无压缩、条带1行、单波段 16bit、Photometric=1(BlackIsZero)、Planar=chunky。
    返回 (路径, W, H)。"""
    W = H = n
    bpp = 2
    row_bytes = W * bpp
    n_strips = H                       # rowsperstrip=1

    # ---- 头部：header(8) + IFD + StripOffsets 数组 + StripByteCounts 数组 ----
    ntags = 11
    ifd_len = 2 + ntags * 12 + 4
    arrays_len = n_strips * 4 * 2      # 273 数组 + 279 数组
    data_start = 8 + ifd_len + arrays_len

    def entry(tag, typ, count, value):
        return struct.pack('<HHI', tag, typ, count) + struct.pack('<I', value)

    ifd = struct.pack('<H', ntags)
    ifd += entry(256, 4, 1, W)                       # ImageWidth
    ifd += entry(257, 4, 1, H)                       # ImageLength
    ifd += entry(258, 3, 1, 16)                      # BitsPerSample
    ifd += entry(259, 3, 1, 1)                       # Compression = none
    ifd += entry(262, 3, 1, 1)                       # Photometric = BlackIsZero
    ifd += entry(273, 4, n_strips, 8 + ifd_len)      # StripOffsets → 数组偏移
    ifd += entry(277, 3, 1, 1)                       # SamplesPerPixel
    ifd += entry(278, 4, 1, 1)                       # RowsPerStrip = 1
    ifd += entry(279, 4, n_strips, 8 + ifd_len + n_strips * 4)  # StripByteCounts → 数组偏移
    ifd += entry(284, 3, 1, 1)                       # PlanarConfiguration = chunky
    ifd += entry(339, 3, 1, 1)                       # SampleFormat = unsigned
    ifd += struct.pack('<I', 0)                      # 下一 IFD = 无

    offsets = bytearray()
    bytecounts = bytearray()
    for i in range(n_strips):
        offsets += struct.pack('<I', data_start + i * row_bytes)
        bytecounts += struct.pack('<I', row_bytes)

    header = b'II' + struct.pack('<H', 42) + struct.pack('<I', 8) + ifd
    header += bytes(offsets) + bytes(bytecounts)

    # ---- 数据：逐行写渐变（x 列 → 值），一行一 flush ----
    with open(path, 'wb') as f:
        f.write(header)
        buf = bytearray(row_bytes)
        scale = 65535.0 / (W - 1)
        for y in range(H):
            # 行内水平渐变；8 列为一组写 uint16 LE
            for x in range(0, W, 8):
                for k in range(8):
                    col = x + k
                    val = int(round(col * scale)) if col < W else 0
                    buf[(x + k) * 2] = val & 0xFF
                    buf[(x + k) * 2 + 1] = (val >> 8) & 0xFF
            f.write(buf)
    return path, W, H


def main():
    out_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    os.makedirs(out_dir, exist_ok=True)
    p, w, h = write_big_u16(os.path.join(out_dir, 'big_u16.tif'))
    size_mb = os.path.getsize(p) / 1e6
    print(f'写出 {p}  {w}x{h}  {size_mb:.1f} MB  (无压缩·条带1行·16bit)')


if __name__ == '__main__':
    main()
