"""Pillow 参考栅格化（供 test-maskgen.js 交叉比对）。
用法：
  python pillow_ref.py ref <spec.json> <out.bin>   # PIL 多边形填充 → 0/1 原始字节
  python pillow_ref.py read <img> <out.bin>        # PIL 读回任意栅格 → 0/255 原始字节
与 backend/services/mask.py 的 rasterize_polygons 保持同一填充语义。
"""
import json
import sys

import numpy as np
from PIL import Image, ImageDraw


def rasterize_polygons(width, height, polygons):
    img = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(img)
    for poly in polygons:
        pts = [(int(x), int(y)) for x, y in poly]
        if len(pts) >= 3:
            draw.polygon(pts, fill=1)
    return np.asarray(img, dtype=np.uint8)


def main():
    cmd = sys.argv[1]
    if cmd == "ref":
        spec_path, out_bin = sys.argv[2], sys.argv[3]
        spec = json.load(open(spec_path, encoding="utf-8"))
        mask = rasterize_polygons(spec["width"], spec["height"], spec["polygons"])
        with open(out_bin, "wb") as f:
            f.write(mask.tobytes())
        print("ref sum", int(mask.sum()), "shape", mask.shape, flush=True)
    elif cmd == "read":
        img_path, out_bin = sys.argv[2], sys.argv[3]
        img = np.asarray(Image.open(img_path))
        if img.ndim > 2:
            img = img[:, :, 0]
        mask = (img > 0).astype(np.uint8) * 255
        with open(out_bin, "wb") as f:
            f.write(mask.tobytes())
        print("read sum", int((mask > 0).sum()), "shape", mask.shape, flush=True)
    else:
        raise SystemExit("unknown cmd " + cmd)


if __name__ == "__main__":
    main()
