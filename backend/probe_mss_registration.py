"""Read-only PAN/MSS registration diagnostics. Run as python -m backend.probe_mss_registration."""
import argparse
import json
import math
import re
from pathlib import Path

from backend.pathguard import PathDeniedError, ensure_allowed, to_posix_array_path
from backend.services import mss_water


TAG_LIMIT = 4096
SIDECAR_LIMIT = 16 * 1024
GRID_TAGS = (*mss_water.SPATIAL_TAGS, 50909, 42113)


def _json_value(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    return value


RPC_SCALARS = {f"{axis}_{kind}" for axis in ("LINE", "SAMP", "LAT", "LONG", "HEIGHT")
               for kind in ("OFF", "SCALE")}
RPC_COEFFICIENTS = {f"{axis}_{kind}_COEFF_{i}" for axis in ("LINE", "SAMP")
                    for kind in ("NUM", "DEN") for i in range(1, 21)}
RPC_FIELDS = RPC_SCALARS | RPC_COEFFICIENTS


def _parse_rpc(text):
    fields = {}
    for line in text.splitlines():
        key, separator, value = line.partition(":")
        key = key.strip().upper()
        if key not in RPC_FIELDS:
            continue
        match = re.fullmatch(r"\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
                             r"(?:\s+(?:pixels|degrees|meters))?\s*", value, re.IGNORECASE)
        if not separator or not match or key in fields:
            return {"status": "invalid_field", "field": key}, None
        number = float(match[1])
        if not math.isfinite(number) or (key.endswith("_SCALE") and number <= 0):
            return {"status": "invalid_field", "field": key}, None
        fields[key] = number
    missing = sorted(RPC_FIELDS - fields.keys())
    if missing:
        return {"status": "incomplete", "missing": missing}, None
    return {"status": "complete"}, fields


def _rpc_summary(text, width, height):
    summary, fields = _parse_rpc(text)
    if not fields:
        return summary
    return {
        **summary,
        "normalization": {key: fields[key] for key in sorted(RPC_SCALARS)},
        "offset_minus_half_size": {"line": fields["LINE_OFF"] - height / 2,
                                   "sample": fields["SAMP_OFF"] - width / 2},
    }


def _sidecar_summary(source, width, height):
    entries = []
    for candidate in sorted(set(mss_water._sidecars(source))):
        record = {"name": candidate.name}
        entries.append(record)
        if candidate.is_symlink() or not candidate.is_file():
            record["status"] = "not_read"
            continue
        checked = ensure_allowed(candidate, kind="file")
        if checked.resolve(strict=True).parent != source.resolve(strict=True).parent:
            record["status"] = "outside_scene"
            continue
        record["size_bytes"] = checked.stat().st_size
        rpc_text = candidate.name.lower().endswith(("_rpc.txt", ".rpc.txt"))
        if rpc_text or candidate.suffix.lower() in {".prj", ".tfw", ".tifw", ".tiffw", ".wld"}:
            with checked.open("rb") as stream:
                data = stream.read(SIDECAR_LIMIT + 1)
            if len(data) > SIDECAR_LIMIT:
                record["status"] = "over_budget"
            elif rpc_text:
                record["rpc"] = _rpc_summary(data.decode("ascii", errors="replace"), width, height)
            else:
                record["text"] = data.decode("utf-8-sig", errors="replace").strip()
    return entries


def _describe(source, tifffile):
    tags = {}
    with tifffile.TiffFile(source) as tif:
        page = tif.pages[0]
        sidecars = _sidecar_summary(source, page.imagewidth, page.imagelength)
        oversized = False
        for code in GRID_TAGS:
            tag = page.tags.get(code)
            if tag is None:
                continue
            record = {"name": tag.name, "count": tag.count}
            tags[str(code)] = record
            if code == 50844:
                continue  # Record RPC presence without loading coefficients.
            if tag.count > TAG_LIMIT:
                record["status"] = "over_budget"
                oversized = True
                continue
            record["value"] = _json_value(tag.value)
        grid = None if oversized or "50844" in tags else mss_water._grid(page)
        summary = {
            "scene": source.parent.name, "name": source.name,
            "width": page.imagewidth, "height": page.imagelength,
            "bands": page.samplesperpixel, "tags": tags, "sidecars": sidecars,
            "supported_embedded_grid": grid is not None,
        }
    return summary, grid


def inspect_registration(pan_path):
    pan, mss, xml, satellite, sensor = mss_water.resolve_mss(pan_path)
    tifffile = mss_water.require_tifffile()
    pan_info, pan_grid = _describe(pan, tifffile)
    mss_info, mss_grid = _describe(mss, tifffile)
    matches = pan_grid is not None and pan_grid == mss_grid and all(map(math.isfinite, pan_grid[2]))
    return {
        "satellite": satellite, "sensor": sensor, "mss_xml": xml.name,
        "pan": pan_info, "mss": mss_info,
        "embedded_grids_match": matches,
        "passes_current_alignment_rules": bool(
            matches and pan_info["bands"] == 1 and not pan_info["sidecars"] and not mss_info["sidecars"]
        ),
    }


def _whole_scene_rpc(source):
    names = {base.casefold() + suffix for base in (source.stem, source.parent.name)
             for suffix in ("_rpc.txt", ".rpc.txt")}
    candidates = {path for path in mss_water._sidecars(source) if path.name.casefold() in names}
    if len(candidates) != 1:
        raise ValueError(f"{source.name}: expected exactly one whole-scene RPC")
    path = candidates.pop()
    if path.is_symlink():
        raise ValueError(f"{path.name}: RPC symlink rejected")
    checked = ensure_allowed(path, kind="file")
    if checked.resolve(strict=True).parent != source.resolve(strict=True).parent:
        raise ValueError(f"{path.name}: RPC outside scene")
    with checked.open("rb") as stream:
        raw = stream.read(SIDECAR_LIMIT + 1)
    if len(raw) > SIDECAR_LIMIT:
        raise ValueError(f"{path.name}: RPC exceeds read budget")
    summary, fields = _parse_rpc(raw.decode("ascii"))
    if fields is None:
        raise ValueError(f"{path.name}: {summary}")
    return fields


def _project_rpc(rpc, longitude, latitude, height):
    # RPC00B term order and pixel-center offsets match GDAL's RPC model.
    l = (longitude - rpc["LONG_OFF"]) / rpc["LONG_SCALE"]
    p = (latitude - rpc["LAT_OFF"]) / rpc["LAT_SCALE"]
    h = (height - rpc["HEIGHT_OFF"]) / rpc["HEIGHT_SCALE"]
    terms = (1, l, p, h, l*p, l*h, p*h, l*l, p*p, h*h,
             l*p*h, l**3, l*p*p, l*h*h, l*l*p, p**3, p*h*h, l*l*h, p*p*h, h**3)

    def axis(name):
        numerator = math.fsum(rpc[f"{name}_NUM_COEFF_{i}"] * t for i, t in enumerate(terms, 1))
        denominator = math.fsum(rpc[f"{name}_DEN_COEFF_{i}"] * t for i, t in enumerate(terms, 1))
        if abs(denominator) < 1e-12:
            raise ValueError("RPC denominator is near zero")
        return rpc[f"{name}_OFF"] + rpc[f"{name}_SCALE"] * numerator / denominator

    point = (axis("SAMP"), axis("LINE"))
    if not all(map(math.isfinite, point)):
        raise ValueError("RPC projection is nonfinite")
    return point


def _inverse_rpc(rpc, pixel, height):
    from scipy.optimize import least_squares

    def ground(point):
        return (rpc["LONG_OFF"] + point[0] * rpc["LONG_SCALE"],
                rpc["LAT_OFF"] + point[1] * rpc["LAT_SCALE"])

    def residual(point):
        x, y = _project_rpc(rpc, *ground(point), height)
        return (x - pixel[0], y - pixel[1])

    result = least_squares(residual, (0., 0.), bounds=(-1.25, 1.25), max_nfev=40)
    error = math.hypot(*result.fun)
    if not result.success or error > 0.05:
        raise ValueError(f"RPC inversion did not converge within 0.05 pixels ({error:.3f})")
    return ground(result.x), error


def rpc_sweep(pan_path, heights):
    if not 2 <= len(heights) <= 9 or not all(map(math.isfinite, heights)):
        raise ValueError("Provide 2-9 finite diagnostic heights")
    pan, mss, _, _, _ = mss_water.resolve_mss(pan_path)
    pan_rpc, mss_rpc = _whole_scene_rpc(pan), _whole_scene_rpc(mss)
    tifffile = mss_water.require_tifffile()
    with tifffile.TiffFile(pan) as tif:
        pw, ph = tif.pages[0].imagewidth, tif.pages[0].imagelength
    with tifffile.TiffFile(mss) as tif:
        mw, mh = tif.pages[0].imagewidth, tif.pages[0].imagelength
    results = []
    for y in (0.1, 0.5, 0.9):
        for x in (0.1, 0.5, 0.9):
            pixel = (x * (mw - 1), y * (mh - 1))
            points = []
            for height in heights:
                try:
                    ground, error = _inverse_rpc(mss_rpc, pixel, height)
                    target = _project_rpc(pan_rpc, *ground, height)
                    pan_ground, _ = _inverse_rpc(pan_rpc, target, height)
                    returned = _project_rpc(mss_rpc, *pan_ground, height)
                    points.append({"height_m": height, "pan_xy": target,
                                   "inside_pan": 0 <= target[0] < pw and 0 <= target[1] < ph,
                                   "mss_inverse_error_px": error,
                                   "mss_round_trip_error_px": math.dist(pixel, returned)})
                except ValueError as exc:
                    points.append({"height_m": height, "error": str(exc)})
            usable = [point["pan_xy"] for point in points if "pan_xy" in point]
            spread = max((math.dist(a, b) for a in usable for b in usable), default=None) if len(usable) > 1 else None
            results.append({"mss_xy": pixel, "pan_height_spread_px": spread, "samples": points})
    return {"usage": "diagnostic_only", "pixel_coordinates": "zero_based_pixel_centers",
            "pan": pan.name, "mss": mss.name, "samples": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pan", help="PAN TIFF path on the production array")
    parser.add_argument("--heights", type=float, nargs="+", help="Run RPC diagnostics at these assumed heights in metres")
    args = parser.parse_args()
    try:
        source = Path(to_posix_array_path(args.pan))
        report = rpc_sweep(source, args.heights) if args.heights else inspect_registration(source)
        print(json.dumps(report, indent=2, ensure_ascii=True, default=str))
    except (PathDeniedError, OSError, ValueError, RuntimeError, IndexError) as exc:
        parser.exit(1, f"{type(exc).__name__}: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
