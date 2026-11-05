"""Read-only tour evidence. Only explicit files, header reads, no pixel decoding."""
import math
from pathlib import Path

import struct

from backend.services.preview_jpg import (source_revision, probe_tiff,
    _parse_tiff_header, _read_ifd, _read_array, PreviewError)


def image_identity(path: Path, sequence: str, role: str) -> dict:
    revision = source_revision(path)
    result = dict(sequence=sequence, role=role, sourceRevision=revision,
                  width=0, height=0,
                  evidenceReason='缺少可验证的 GeoTIFF 全幅配准证据；同目录关联不等于配准证明')
    try:
        info = probe_tiff(path)
        result.update(width=info['W'], height=info['H'])
        big, endian, offset = _parse_tiff_header(path)
        scalars, arrays = _read_ifd(path, big, endian, offset)
        # Only north-up, single tiepoint, PixelIsArea with an EPSG CRS is
        # supported. Header reads avoid Pillow's large-image pixel limit.
        if 34264 in scalars or 34264 in arrays:
            return result

        def doubles(tag, count):
            ref = arrays.get(tag)
            if not ref or ref[:2] != (count, 8):
                return ()
            _, _, location = ref
            if isinstance(location, bytes):
                data = location
            else:
                with path.open('rb') as stream:
                    stream.seek(location)
                    data = stream.read(count * 8)
            return struct.unpack(('<' if endian == 'little' else '>') + 'd' * count, data)

        scale, tie = doubles(33550, 3), doubles(33922, 6)
        key_ref = arrays.get(34735)
        if not key_ref or key_ref[0] > 256 or key_ref[1] != 2:
            return result
        keys = _read_array(path, key_ref, endian).tolist()
        if len(scale) != 3 or len(tie) != 6 or len(keys) < 4:
            return result
        if keys[:3] != [1, 1, 0] or len(keys) != 4 + 4 * keys[3]:
            return result
        geo = {keys[i]: keys[i + 3] for i in range(4, len(keys), 4)
               if keys[i + 1] == 0 and keys[i + 2] == 1}
        epsg = geo.get(3072) if geo.get(1024) == 1 else geo.get(2048) if geo.get(1024) == 2 else None
        if geo.get(1025) != 1 or not epsg or epsg == 32767:
            return result
        sx, sy, _ = scale
        i, j, _, x, y, _ = tie
        if not all(math.isfinite(v) for v in (*scale, *tie)) or sx <= 0 or sy <= 0:
            return result
        left, top = x - i * sx, y + j * sy
        crs = f"EPSG:{epsg}"
        extent = [left, top - info['H'] * sy, left + info['W'] * sx, top]
        if not all(math.isfinite(v) for v in extent):
            return result
        result['registration'] = dict(
            reference=f"geotiff:{crs}:PixelIsArea", crs=crs,
            extent=extent, orientation=[1, 0, 0, -1])
        result.pop('evidenceReason', None)
        if source_revision(path) != revision:
            result.pop('registration', None)
            result['sourceRevision'] = ''
    except (OSError, ValueError, AttributeError, TypeError, KeyError, struct.error, PreviewError):
        pass
    return result
