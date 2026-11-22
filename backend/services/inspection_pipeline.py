"""Read-only QC pipeline sharing the local action runner and Todo ledger."""
from backend.services import batch_detector, batch_manifest
from backend.services.batch_worker import _open_item, ItemBlocked


def create_inspection(store, manifest, manifest_ref, session_id, request_key):
    items = batch_manifest.plan_goal_items(manifest, [])
    return store.create_batch_goal(
        title="只读质检", request_key=f"inspection:{session_id}:{request_key}",
        content_sha256=manifest["manifest_sha256"],
        checkpoint={"pipeline": "inspection", "mode": manifest["mode"], "manifest_ref": manifest_ref},
        session_id=session_id, execution_owner="local_agent",
        items=[{
            **item,
            "checkpoint": {
                "target_image": item.get("target_image"),
                "reference_image": item.get("reference_image"),
                "scene_ref": item.get("scene_ref"),
                "config": item.get("config") or {},
                "exclusion_reason": item.get("exclusion_reason"),
            },
        } for item in items])


def process_inspection(state, goal, lease, choose):
    store = state.store
    gid = goal["goal_id"]
    pending = store.list_goal_items(gid, statuses=("PENDING", "DETECTING"))
    active = next((item for item in pending if item["status"] == "DETECTING"), None)
    if not pending:
        failures = store.goal_item_status_counts(gid).get("BLOCKED", 0)
        counts = store.goal_item_status_counts(gid)
        store.complete_verified_goal(
            gid, {"ok": True, "scope": "inspection", "counts": counts}, phase="done",
            checkpoint={**goal["checkpoint"], "monitoring_done": True, "blocked_items": failures},
            expected_version=lease.version, lease_token=lease.token, release=True)
        return False
    if active is None:
        selected = choose(state, goal, lease, [(i, "inspect_item") for i in pending])
        if selected is None:
            return False
        item, _ = selected
        active = store.transition_goal_item(
            gid, step_id=item["step_id"], expected_version=item["version"],
            status="DETECTING", lease_token=lease.token)
        if active is None:
            return False
    cp = dict(active["checkpoint"])
    try:
        _open_item(cp)
        target = {**cp["target_image"], "item_id": active["item_id"]}
        detected = batch_detector.detect(
            target, cp["reference_image"],
            {**cp["config"].get("detection", {}), "execution_mode": goal["checkpoint"]["mode"]})
        candidate_count = len(detected.get("candidates") or [])
        unprocessed = (detected.get("candidate_accounting") or {}).get("unprocessed", 0)
        if unprocessed:
            raise ValueError("差异候选未全部处理")
        status, error = "SUCCEEDED", None
        receipt = {"candidate_count": candidate_count, "provenance": detected.get("provenance")}
        cp["inspection"] = receipt
    except (ItemBlocked, ValueError, OSError, batch_detector.DetectionError) as exc:
        status, error = "BLOCKED", str(exc)
        receipt = {"error": error}
    return bool(store.transition_goal_item(
        gid, step_id=active["step_id"], expected_version=active["version"],
        status=status, lease_token=lease.token, checkpoint=cp, error=error,
        extra_events=[("inspection_result", {"item_id": active["item_id"], **receipt})]))
