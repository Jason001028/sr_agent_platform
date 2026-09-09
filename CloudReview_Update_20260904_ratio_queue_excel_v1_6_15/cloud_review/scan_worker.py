from __future__ import annotations

import argparse
import json
import os
import time
import traceback
from datetime import datetime
from pathlib import Path

from .core import (
    AppConfig,
    MySQLDatasetStore,
    ReviewStore,
    cache_review_scene,
    cleanup_cache,
    discover_scan_groups,
    discover_scenes_parallel,
)


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    last_error: PermissionError | None = None
    for delay in (0.0, 0.05, 0.1, 0.2):
        try:
            path.write_text(serialized, encoding="utf-8")
            return
        except PermissionError as exc:
            last_error = exc
            if delay:
                time.sleep(delay)
    if last_error is not None:
        raise last_error


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def read_session_seen(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    try:
        return {
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    except OSError:
        return set()


def append_session_seen(path: Path | None, scene_id: str) -> None:
    if path is None or not scene_id:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{scene_id}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--progress", required=False)
    parser.add_argument("--log", required=False)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--session-path", default="")
    args = parser.parse_args()

    result_path = Path(args.result)
    progress_path = Path(args.progress) if args.progress else result_path.with_name("scan_progress.json")
    log_path = Path(args.log) if args.log else result_path.with_name("scan.log")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        started = time.perf_counter()
        config = AppConfig.load(Path(args.config))
        store = ReviewStore(Path(args.state))
        cleared_cache = store.clear_scan_cache()
        store.reset_in_progress_scan_groups()
        available_groups = discover_scan_groups(config)
        group = store.next_scan_group(available_groups)
        if group is None:
            payload = {
                "ok": True,
                "running": False,
                "run_id": args.run_id,
                "count": 0,
                "review_count": 0,
                "removed": 0,
                "freed": 0,
                "elapsed_seconds": round(time.perf_counter() - started, 2),
                "cleared_cache": cleared_cache,
                "all_groups_completed": True,
                "last_log": "所有日期/卫星分组已经扫描完成",
                "log_path": str(log_path),
                "directories_scanned": 0,
                "files_seen": 0,
                "known_skipped": 0,
                "candidate_groups": 0,
                "emitted": 0,
                "limited": 0,
                "date_dirs_scanned": 0,
                "satellite_dirs_scanned": 0,
                "scene_dirs_scanned": 0,
            }
            write_json(result_path, payload)
            write_json(progress_path, payload)
            return 0
        store.mark_scan_group_started(group)
        known_ids = store.known_scene_ids(config.scan_lookback_days)
        session_seen_ids = store.monitor_seen_ids(args.session_id)
        try:
            known_ids.update(MySQLDatasetStore(config).scene_ids())
        except Exception:
            pass
        known_ids.update(session_seen_ids)
        total_count = 0
        review_count = 0
        session_written = 0
        last_log = (
            f"扫描启动，分组 {group.group_date}/{group.satellite}，"
            f"已清理历史候选缓存 {cleared_cache} 条，"
            f"本次监控已检查 {len(session_seen_ids)} 景"
        )

        def publish(extra: dict[str, object]) -> None:
            current_counts = store.counts()
            pending_total = int(current_counts.get("pending", 0) or 0) + int(
                current_counts.get("manual", 0) or 0
            )
            payload = {
                "ok": True,
                "running": True,
                "run_id": args.run_id,
                "count": pending_total,
                "review_count": review_count,
                "elapsed_seconds": round(time.perf_counter() - started, 2),
                "scan_group_date": group.group_date,
                "scan_group_satellite": group.satellite,
                "session_seen": len(session_seen_ids),
                "session_written": session_written,
                "last_log": last_log,
                "log_path": str(log_path),
                **extra,
            }
            write_json(progress_path, payload)

        def log(message: str) -> None:
            nonlocal last_log
            last_log = message
            append_log(log_path, message)

        def on_batch(scenes):
            nonlocal total_count, review_count
            for scene in scenes:
                scene.review_batch = args.session_id or args.run_id
                if scene.status in {"pending", "manual"}:
                    try:
                        cache_path = cache_review_scene(scene, config)
                        scene.cloud_ratios["review_cache_path"] = cache_path
                    except Exception as exc:
                        log(f"复核缓存失败 {scene.scene_id} | {exc}")
            total_count += store.upsert_scenes(scenes)
            review_count += sum(
                1 for scene in scenes if scene.status in {"pending", "manual"}
            )
            publish({"last_batch": len(scenes)})

        def on_progress(scan_stats):
            publish(scan_stats)

        def on_seen(scene_id: str) -> None:
            nonlocal session_written
            if scene_id in session_seen_ids:
                return
            session_seen_ids.add(scene_id)
            session_written += 1
            store.mark_monitor_seen(args.session_id, scene_id)

        log(last_log)
        publish(
            {
                "directories_scanned": 0,
                "files_seen": 0,
                "known_skipped": 0,
                "candidate_groups": 0,
                "emitted": 0,
                "limited": 0,
                "date_dirs_scanned": 0,
                "satellite_dirs_scanned": 0,
                "scene_dirs_scanned": 0,
                "current_scene": "",
                "current_decision": "",
                "current_difference": "",
                "cleared_cache": cleared_cache,
                "scan_group_date": group.group_date,
                "scan_group_satellite": group.satellite,
            }
        )
        _scenes, scan_stats = discover_scenes_parallel(
            config,
            known_scene=known_ids.__contains__,
            on_seen=on_seen,
            on_batch=on_batch,
            on_progress=on_progress,
            on_log=log,
            batch_size=1,
            scan_group=group,
        )
        store.mark_scan_group_completed(
            group,
            int(scan_stats.get("scene_dirs_scanned", 0) or 0),
            review_count,
        )
        removed, freed = cleanup_cache(config)
        elapsed_seconds = round(time.perf_counter() - started, 2)
        log(
            f"扫描完成：本轮扫描景目录 {scan_stats.get('scene_dirs_scanned', 0)} 个，"
            f"本轮新增待复核 {review_count} 景，耗时 {elapsed_seconds:.2f}s，"
            f"平均每景 {scan_stats.get('avg_scene_seconds', 0):.2f}s"
        )
        current_counts = store.counts()
        pending_total = int(current_counts.get("pending", 0) or 0) + int(
            current_counts.get("manual", 0) or 0
        )
        payload = {
            "ok": True,
            "running": False,
            "run_id": args.run_id,
            "count": pending_total,
            "review_count": review_count,
            "removed": removed,
            "freed": freed,
            "elapsed_seconds": elapsed_seconds,
            "cleared_cache": cleared_cache,
            "scan_group_date": group.group_date,
            "scan_group_satellite": group.satellite,
            "all_groups_completed": False,
            "session_seen": len(session_seen_ids),
            "session_written": session_written,
            "last_log": last_log,
            "log_path": str(log_path),
            **scan_stats,
        }
        exit_code = 0
    except Exception as exc:
        try:
            if "store" in locals() and "group" in locals() and group is not None:
                store.mark_scan_group_failed(group)
        except Exception:
            pass
        append_log(log_path, f"扫描失败：{exc}")
        payload = {
            "ok": False,
            "run_id": args.run_id,
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "log_path": str(log_path),
        }
        exit_code = 1
    write_json(result_path, payload)
    write_json(progress_path, payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
