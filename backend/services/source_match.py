"""Bounded content sampling for explicitly linking a dropped TIFF to a server file."""
import base64
from pathlib import Path

SAMPLE_BYTES = 4096


def verify_source(path: Path, fingerprint: dict) -> None:
    """Check name, size and three deterministic samples; not a full-file checksum."""
    before = path.stat()
    size = before.st_size
    if not isinstance(fingerprint, dict) or fingerprint.get("name") != path.name or fingerprint.get("size") != size:
        raise ValueError("文件名或字节数不符，不能关联")
    offsets = [0, max(0, (size - SAMPLE_BYTES) // 2), max(0, size - SAMPLE_BYTES)]
    samples = fingerprint.get("samples")
    if not isinstance(samples, list) or len(samples) != 3:
        raise ValueError("缺少三段文件内容采样")
    with path.open("rb") as stream:
        for offset, sample in zip(offsets, samples):
            if not isinstance(sample, str) or len(sample) > 5464:
                raise ValueError("文件内容采样无效")
            try:
                data = base64.b64decode(sample, validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("文件内容采样无效") from exc
            stream.seek(offset)
            if data != stream.read(SAMPLE_BYTES):
                raise ValueError("文件内容采样不符，不能关联同名文件")
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("源文件在核对期间发生变化，请重试")
