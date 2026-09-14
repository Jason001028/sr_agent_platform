"""Scene search over the satellite-image archive (intranet disk array).

Defines the search interface and provides two backends behind it:

  * disk — walk a real directory (set SR_SCENES_ROOT) for TIFF/IMG/JPG files and
    parse scene metadata (satellite, sensor, date) from the filename. This is
    the real-array shape; pointing the root at the array mount is all the
    "integration" needed.
  * fake — deterministic synthetic rows used on the dev machine where the
    array is unreachable. Fake rows carry `fake: True` so callers never
    mistake placeholder paths for real files.

Filename convention (matches SR_code, e.g.
JL1KF02B03_PMS05_20260722125045_200524168_102_0034_001_L1_PAN_mask.txt):
the first `_`-separated parts are satellite and sensor, and the first
8..14-digit run is the acquisition timestamp (YYYYMMDD...). Unknown parts → None.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

# Raster extensions treated as scenes (TIFF primary; IMG common on the array).
_RASTER_EXTS = {".tif", ".tiff", ".img"}
# 最小原型 §4.7：盘阵目录里预生成的 JPG 也要能列出并直接打开（8bit 显示就绪，
# 不需要后端再烘焙预览）。真值见 docs/status/phase4 —— 盘阵读 JPG 是既有做法。
_IMAGE_EXTS = {".jpg", ".jpeg"}
_SCENE_EXTS = _RASTER_EXTS | _IMAGE_EXTS
# 派生件不算场景（两种，都是「别的东西的输入/产物」，不是可提交对象）：
#   1) 后端自己缓存的 <basename>.preview.jpg（api/paths.preview_jpg_path 的产物）——
#      列出来只会在同一张图上多出一行、且 jpgUrl 指向缓存而非源文件。
#   2) 场景目录里预生成的掩膜 <名字>_mask.<ext>（最小原型 §4.3：这是「提交 SR」的*输入*，
#      不是可提交的场景）。真机目录里它和场景同名同目录，列出来会多一行：卫星/传感器
#      从掩膜文件名解析（satellite=<父目录名>、sensor="mask"）、尺寸取掩膜 TIFF 头，
#      且「提交 SR」可点（同目录 → 同指纹 → 幂等命中，不至于重复跑，但列表是脏的）。
_DERIVED_SUFFIX = ".preview.jpg"
_DERIVED_STEM_SUFFIX = "_mask"
_TS_RE = re.compile(r"\d{8,14}")


def is_scene_file(path) -> bool:
    """该文件是否算一个可列出的盘阵场景（确在盘上 + 后缀白名单 + 排除派生件）。"""
    p = Path(path)
    if not p.is_file():
        return False
    if p.suffix.lower() not in _SCENE_EXTS:
        return False
    if p.name.lower().endswith(_DERIVED_SUFFIX):
        return False
    return not p.stem.lower().endswith(_DERIVED_STEM_SUFFIX)


def parse_filename(path) -> dict:
    """Extract {satellite, sensor, date} from a scene filename; unknown → None."""
    parts = Path(path).stem.split("_")
    sat = parts[0] if parts else None
    sensor = parts[1] if len(parts) > 1 else None
    date = None
    ts = next((p for p in parts if _TS_RE.fullmatch(p)), None)
    if ts:
        try:
            datetime.strptime(ts[:8], "%Y%m%d")
            date = f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}"
        except ValueError:
            date = None
    return {"satellite": sat, "sensor": sensor, "date": date}


def scan_root(root) -> list[dict]:
    """Recursively list raster scenes under root with parsed metadata."""
    scenes = []
    if not root:
        return scenes
    base = Path(root)
    if not base.is_dir():
        return scenes
    for p in sorted(base.rglob("*")):
        if is_scene_file(p):
            meta = parse_filename(p)
            scenes.append({**meta, "id": p.stem, "path": str(p),
                           "size_bytes": p.stat().st_size, "fake": False})
    return scenes


def fake_scenes(n=12) -> list[dict]:
    """Deterministic synthetic scene rows for local testing (fake=True)."""
    combos = [
        ("GF07A03", "PMS01"), ("KF02B04", "PMS05"), ("JL1KF02B03", "PAN"),
        ("GF04", "PMS02"), ("ZY302", "MUX"), ("GF07A03", "PAN"),
    ]
    dates = ["20260722", "20260723", "20260724", "20260801", "20260805", "20260810"]
    scenes = []
    for i in range(n):
        sat, sensor = combos[i % len(combos)]
        # date advances every len(combos) rows → no duplicate scene ids
        ts = f"{dates[(i // len(combos)) % len(dates)]}120000"
        sid = f"{sat}_{sensor}_{ts}"
        scenes.append({"id": sid, "path": f"<fake>/{sid}.tif",
                       "satellite": sat, "sensor": sensor,
                       "date": f"{ts[0:4]}-{ts[4:6]}-{ts[6:8]}",
                       "size_bytes": 0, "fake": True})
    return scenes


def search_scenes(root, query="", satellite=None, sensor=None, date_from=None,
                  date_to=None, limit=20):
    """Search the archive; returns {"source", "scanned", "count", "results"}.

    root — real directory (disk backend) or None/not-a-dir (falls back to fake).
    query — case-insensitive substring on scene id/path.
    satellite — case-insensitive substring on the parsed satellite id.
    sensor — case-insensitive substring on the parsed sensor id.
    date_from/date_to — inclusive "YYYY-MM-DD" bounds; scenes without a parsed
    date are excluded once a bound is given.
    """
    base = Path(root) if root else None
    if base and base.is_dir():
        scenes = scan_root(root)
        source = "disk"
    else:
        scenes = fake_scenes()
        source = "fake"

    q = (query or "").strip().lower()
    sat = (satellite or "").strip().lower()
    sen = (sensor or "").strip().lower()

    def keep(s):
        if q and q not in s["id"].lower() and q not in s["path"].lower():
            return False
        if sat and sat not in (s.get("satellite") or "").lower():
            return False
        if sen and sen not in (s.get("sensor") or "").lower():
            return False
        if s.get("date"):
            if date_from and s["date"] < date_from:
                return False
            if date_to and s["date"] > date_to:
                return False
        elif date_from or date_to:
            return False
        return True

    matched = [s for s in scenes if keep(s)]
    matched.sort(key=lambda s: (s.get("date") or "", s["id"]), reverse=True)
    return {"source": source, "scanned": len(scenes), "count": len(matched),
            "results": matched[:limit]}
