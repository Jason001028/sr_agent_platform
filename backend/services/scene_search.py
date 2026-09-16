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

What counts as a scene (disk backend) is a whitelist, not a blacklist: the file
must sit in a *scene directory* — one containing `<dirname>_meta.xml` — and be
named either `<dirname>.<ext>` (the SC step's input) or `PAN.<ext>` (the RC
step's). Everything else in a scene directory is something else's input or
output: SR products and the input backup (_sr/_NOSR/_ori), the cloud map
(_cloud), thumbnails (_thumb), the ROI mask (_mask — it is an *input* to
"submit SR", not a scene), the backend's own `<stem>.preview.jpg` cache, and the
dozens of debug renders under `Debug/`. See is_scene_file.
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
#: 场景目录的判据：目录里躺着一份 <目录名>_meta.xml。SR 脚本靠它判 RC/SC
#: （util.check_sr_previous_step），没有它的目录提交也跑不起来 —— 所以它同时也是
#: 「这个目录里的东西能不能提交」的判据。
_META_SUFFIX = "_meta.xml"

#: RC 步骤的输入文件名（util.get_l1_pan_tif_rcsc 的 RC 分支读的就是 PAN.tif）。
_RC_INPUT_STEM = "pan"

_TS_RE = re.compile(r"\d{8,14}")


def is_scene_dir(path) -> bool:
    """该目录是不是一个可提交的场景目录（内含 <目录名>_meta.xml）。"""
    d = Path(path)
    return d.is_dir() and (d / (d.name + _META_SUFFIX)).is_file()


def is_scene_file(path) -> bool:
    """该文件是否算一个可列出的盘阵场景。

    白名单，两条同时成立：所在目录是场景目录（is_scene_dir），且文件名要么等于目录名
    （SC 步骤的输入 `<目录名>.<ext>`），要么是 `PAN.<ext>`（RC 步骤的输入）。

    这样一次挡住全部「别的东西的输入/产物」：SR 产物与输入备份（_sr/_NOSR/_ori）、
    云量图（_cloud）、缩略图（_thumb）、提交 SR 的输入掩膜（_mask）、后端自己烘焙的
    `<stem>.preview.jpg` 缓存，以及 Debug/ 下十几张调试图。此前用的是黑名单，每冒出
    一类新派生件就得补一条 —— 2026-09-15 真机接上盘阵时，18 行里有 16 行是这种脏数据。
    """
    p = Path(path)
    if not p.is_file():
        return False
    if p.suffix.lower() not in _SCENE_EXTS:
        return False
    if not is_scene_dir(p.parent):
        return False
    stem = p.stem.lower()
    return stem == p.parent.name.lower() or stem == _RC_INPUT_STEM


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
