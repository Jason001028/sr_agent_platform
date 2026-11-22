"""Prepare and revalidate land-constrained artifacts for a repair plan."""
from pathlib import Path

from backend.services import batch_manifest, batch_mask


def prepare_item(item, mode):
    from backend.services import batch_worker, mss_land

    batch_worker._open_item(item)
    target = {**item["target_image"], "item_id": item["item_id"],
              "reference_image": item["reference_image"]}
    pan = Path(target["path"])
    source = mss_land.preflight(pan)
    nosr = Path(item["reference_image"]["path"])
    nosr_source = file_revision(nosr, linked=True)
    scene = Path(item["scene_ref"])
    pan_metadata = file_revision(scene / (scene.name + "_meta.xml"))
    config = item.get("config") or {}
    key = batch_manifest.canonical_hash({
        "source": source["revision"], "target": target, "config": config, "mode": mode,
    })
    root = batch_manifest.artifact_root() / "land-plans" / key
    result = batch_worker._detection_job({
        "mode": mode, "target_image": target,
        "candidate_root": str(root / "candidate"),
        "detection_config": config.get("detection") or {},
        "rows_per_strip": config.get("mask_rows_per_strip"),
        "overlay_max_edge": config.get("mask_overlay_max_edge"),
    })
    if result.get("incomplete"):
        raise ValueError("原始差分候选未处理完整")
    detection, candidate = result["detection"], result["mask"]
    execution_key = batch_mask.candidate_digest(detection)
    evidence = mss_land.build(
        pan, Path(candidate["path"]) if candidate else None,
        root / "execution" / execution_key, detection.get("candidates") or [])
    if evidence["revision"] != source["revision"] or file_revision(nosr, linked=True) != nosr_source:
        raise ValueError("生成掩膜期间源影像已变化")
    if candidate and evidence["candidate_mask"]["sha256"] != candidate["sha256"]:
        raise ValueError("原始候选掩膜在生成执行掩膜期间已变化")
    mask = execution_mask(candidate, evidence, target)
    prepared = {"evidence": evidence, "mask": mask, "candidate_mask": candidate,
                "detection": detection, "pan_path": str(pan), "nosr": nosr_source,
                "pan_metadata": pan_metadata}
    verify_item(prepared)
    batch_worker._open_item(item)
    return prepared


def verify_item(prepared, *, contents=True):
    from backend.services import mss_land

    if file_revision(prepared["nosr"]["path"], linked=True) != prepared["nosr"]:
        raise ValueError("原始 NOSR 已变化")
    if file_revision(prepared["pan_metadata"]["path"]) != prepared["pan_metadata"]:
        raise ValueError("PAN 生产元数据已变化")
    mss_land.verify(Path(prepared["pan_path"]), prepared["evidence"], contents=contents)


def execution_mask(candidate, evidence, target):
    if not evidence["stats"]["executable"]:
        return None
    final = evidence["final_mask"]
    checked = batch_mask.verify_mask(
        final["path"], width=target["width"], height=target["height"],
        sha256=final["sha256"])
    # The original candidate overlay remains bound to the original candidate.
    mask = {**candidate, **checked,
            "candidate_count": evidence["stats"]["executable"]}
    mask.pop("overlay", None)
    return mask


def file_revision(path, *, linked=False):
    path = Path(path).resolve(strict=True)
    stat = path.stat()
    identity = {"path": str(path), "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino}
    # Staging a private hard link changes POSIX ctime without changing NOSR.
    if not linked:
        identity["ctime_ns"] = stat.st_ctime_ns
    return identity


def restrict_review(cp, candidate, detection):
    """Freeze the reviewed subset under the same land constraint."""
    from backend.services import mss_land

    previous = cp["land_constraint"]
    pan = Path(cp["target_image"]["path"])
    verify_item({"evidence": previous, "pan_path": pan, "nosr": cp["land_nosr"],
                 "pan_metadata": cp["land_pan_metadata"]})
    path = Path(candidate["path"])
    evidence = mss_land.build(pan, path, path.parent.with_name(path.parent.name + "-land"),
                              detection["candidates"])
    if (previous["revision"] != evidence["revision"]
            or candidate["sha256"] != evidence["candidate_mask"]["sha256"]):
        raise ValueError("复核期间陆地约束或候选掩膜已变化，请重新准备计划")
    return evidence, execution_mask(candidate, evidence, cp["target_image"])
