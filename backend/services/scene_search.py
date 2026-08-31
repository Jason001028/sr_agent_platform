"""Scene search over the satellite-image archive (intranet disk array).

Defines the search interface and provides two backends behind it:

  * disk — walk a real directory (set SR_SCENES_ROOT) for TIFF/IMG files and
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
_SCENE_EXTS = {".tif", ".tiff", ".img"}
_TS_RE = re.compile(r"\d{8,14}")


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
        if p.suffix.lower() in _SCENE_EXTS and p.is_file():
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


def search_scenes(root, query="", satellite=None, date_from=None, date_to=None,
                  limit=20):
    """Search the archive; returns {"source", "scanned", "count", "results"}.

    root — real directory (disk backend) or None/not-a-dir (falls back to fake).
    query — case-insensitive substring on scene id/path.
    satellite — case-insensitive substring on the parsed satellite id.
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

    def keep(s):
        if q and q not in s["id"].lower() and q not in s["path"].lower():
            return False
        if sat and sat not in (s.get("satellite") or "").lower():
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
