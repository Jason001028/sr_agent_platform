"""Pre-copy a frozen NOSR for a batch item on the shared artifact volume."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from backend.pathguard import ensure_allowed
from backend.services import batch_manifest, preview_jpg
from backend.services.spatial_identity import resource_identity


def directory(goal_id, item):
    ref = item["checkpoint"]["reference_image"]
    key = hashlib.sha256(json.dumps([
        goal_id, item["item_id"], ref["path"], ref["image_key"], ref["revision"],
    ]).encode("utf-8")).hexdigest()
    return batch_manifest.artifact_root().resolve() / "nosr-stage" / key


def signature(path):
    st = path.stat()
    return [st.st_size, st.st_mtime_ns, st.st_ino]


def ready(folder, reference):
    """Read the descriptor only after the image and descriptor were published."""
    path = folder / "nosr.tif"
    if not folder.exists():
        return None
    data = json.loads((folder / "source.json").read_text(encoding="utf-8"))
    if (data["source_image_key"] != reference["image_key"]
            or data["source_revision"] != reference["revision"]
            or Path(data["source_path"]) != Path(reference["path"]).resolve()
            or path.resolve(strict=True) != path
            or signature(path) != data["signature"]):
        raise ValueError("NOSR 暂存副本已改变，无法复用")
    return {**data, "path": path.as_posix()}


def assert_source(reference):
    source = ensure_allowed(Path(reference["path"]), kind="file")
    if (resource_identity(source)["image_key"] != reference["image_key"]
            or preview_jpg.source_revision(source) != reference["revision"]):
        raise ValueError("原 NOSR 与差异检测时的版本不一致")
    return source


def stage(folder, reference, stopped):
    source = assert_source(reference)
    if ready(folder, reference):
        return
    folder.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".copy-", dir=folder.parent))
    try:
        before = signature(source)
        ctime = source.stat().st_ctime_ns
        target = temp / "nosr.tif"
        with source.open("rb") as src, target.open("wb") as dst:
            while not stopped.is_set():
                block = src.read(8 * 1024 * 1024)
                if not block:
                    break
                dst.write(block)
            if stopped.is_set():
                return
            dst.flush()
            os.fsync(dst.fileno())
        assert_source(reference)
        copied = signature(target)
        if signature(source) != before or copied[0] != before[0]:
            raise ValueError("NOSR 暂存期间输入发生变化或副本不完整")
        data = {"source_path": source.as_posix(), "source_image_key": reference["image_key"],
                "source_revision": reference["revision"],
                "source_signature": before, "source_ctime_ns": ctime, "signature": copied}
        (temp / "source.json").write_text(json.dumps(data), encoding="utf-8")
        if stopped.is_set():
            return
        try:
            temp.rename(folder)
        except OSError:
            if not ready(folder, reference):
                raise
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def discard(folder):
    if folder.is_dir() and not folder.is_symlink():
        (folder / "nosr.tif").unlink(missing_ok=True)
        (folder / "source.json").unlink(missing_ok=True)
        folder.rmdir()
