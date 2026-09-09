from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import mimetypes
import os
import re
import threading
import time
import uuid
import webbrowser
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from PIL import Image
from openpyxl import Workbook

from .core import (
    AppConfig,
    ReviewStore,
    Scene,
    cloud_ratio,
    compare_mask_images,
    copy_to_pending_import,
    difference_percent_from_ratios,
    find_scene_candidate_directories,
    find_scene_directories_by_ids,
    find_scene_metadata_xml,
    filesystem_path,
    geo_cell_from_center,
    open_image_file,
    path_is_file,
    read_mask,
    read_shp_geography,
    read_xml_geography,
    scenes_from_directories,
    search_scene_candidates,
    search_scene_candidates_by_ids,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "configs" / "cloud_review.json"
DEFAULT_STATE_PATH = ROOT / "data" / "cloud_review.sqlite3"
STATIC_ROOT = Path(__file__).resolve().parent / "web_static"
METHOD_LABELS = {
    "combined": "综合法",
    "rdc": "RDC",
    "omnicloudmask": "Omni",
    "final": "最终结果",
}
MASK_COLORS = {
    "combined": (244, 63, 94),
    "rdc": (6, 182, 212),
    "omnicloudmask": (250, 204, 21),
    "final": (168, 85, 247),
}


def _file_content_digest(path: Path | str) -> str:
    """Return a fast content fingerprint without streaming a large network TIF."""
    digest = hashlib.sha1()
    with open(filesystem_path(path), "rb") as stream:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        digest.update(str(size).encode("ascii"))
        sample_size = 64 * 1024
        if size <= sample_size * 4:
            stream.seek(0)
            digest.update(stream.read())
        else:
            offsets = (0, max(0, size // 2 - sample_size // 2), size - sample_size)
            for offset in offsets:
                stream.seek(offset)
                digest.update(str(offset).encode("ascii"))
                digest.update(stream.read(sample_size))
    return digest.hexdigest()


def _resolve_config_paths(config: AppConfig, config_path: Path) -> None:
    base = config_path.resolve().parent.parent
    config.watch_roots = [
        str((base / value).resolve()) if not Path(value).is_absolute() else value
        for value in config.watch_roots
    ]
    for field_name in (
        "database_dir",
        "dataset_output_dir",
        "typical_dir",
        "cache_dir",
        "omnicloudmask_model_dir",
    ):
        value = str(getattr(config, field_name, "") or "")
        if value and not Path(value).is_absolute():
            setattr(config, field_name, str((base / value).resolve()))


def _scene_product_directory(scene: Scene) -> Path | None:
    for value in (scene.source_tif_path, scene.image_path, scene.metadata_path):
        if not value:
            continue
        parent = Path(value).parent
        if parent.name.lower() == scene.scene_id.lower():
            return parent
    return None


def _fixed_combined_and_final_paths(scene: Scene) -> dict[str, str]:
    scene_dir = _scene_product_directory(scene)
    if scene_dir is None:
        return {
            method: str(scene.masks.get(method, "") or "")
            for method in ("combined", "final")
            if scene.masks.get(method) and path_is_file(scene.masks[method])
        }
    candidates = {
        "combined": scene_dir / "Debug" / "Cloud" / "combined" / f"{scene.scene_id}_Cloud.tif",
        "final": scene_dir / f"{scene.scene_id}_cloud.TIF",
    }
    return {
        method: str(path)
        for method, path in candidates.items()
        if path_is_file(path)
    }


def _final_mask_path(scene: Scene, config: AppConfig) -> str:
    return _fixed_combined_and_final_paths(scene).get("final", "")


def _scene_mask_paths(scene: Scene, config: AppConfig) -> dict[str, str]:
    masks = {
        method: str(scene.masks.get(method, "") or "")
        for method in ("rdc", "omnicloudmask")
        if scene.masks.get(method) and path_is_file(scene.masks[method])
    }
    masks.update(_fixed_combined_and_final_paths(scene))
    return masks


def _scene_payload(scene: Scene, config: AppConfig) -> dict[str, object]:
    masks = _scene_mask_paths(scene, config)
    corners = scene.corners or {}
    ordered = [
        corners.get("upper_left"),
        corners.get("upper_right"),
        corners.get("lower_right"),
        corners.get("lower_left"),
    ]
    has_geometry = all(
        isinstance(point, list)
        and len(point) >= 2
        and all(isinstance(value, (int, float)) for value in point[:2])
        for point in ordered
    )
    axis_aligned = False
    rotation_degrees = None
    if has_geometry:
        upper_left, upper_right, lower_right, lower_left = ordered
        tolerance = 1e-8
        axis_aligned = (
            abs(upper_left[1] - upper_right[1]) <= tolerance
            and abs(lower_left[1] - lower_right[1]) <= tolerance
            and abs(upper_left[0] - lower_left[0]) <= tolerance
            and abs(upper_right[0] - lower_right[0]) <= tolerance
        )
        rotation_degrees = math.degrees(
            math.atan2(
                -(upper_right[1] - upper_left[1]),
                upper_right[0] - upper_left[0],
            )
        )
    ratios = {
        method: float(scene.cloud_ratios.get(method, 0.0) or 0.0)
        for method in METHOD_LABELS
        if method in scene.cloud_ratios
    }
    available_methods = [method for method in METHOD_LABELS if method in masks]
    ratio_state = (
        "complete"
        if all(method in ratios for method in available_methods)
        else "pending"
    )
    return {
        "scene_id": scene.scene_id,
        "satellite": scene.satellite,
        "acquired_date": scene.acquired_date,
        "plan_code": scene.plan_code,
        "status": scene.status,
        "center": [scene.center_lon, scene.center_lat],
        "corners": ordered if has_geometry else [],
        "has_geometry": has_geometry,
        "geometry_source": scene.metadata_path,
        "geometry_shape": (
            "axis_aligned" if axis_aligned else "rotated"
        ) if has_geometry else "unknown",
        "geometry_rotation_degrees": rotation_degrees,
        "geometry_diagnostic": str(getattr(scene, "geometry_diagnostic", "") or ""),
        "has_image": path_is_file(scene.image_path),
        "available_masks": available_methods,
        "mask_paths": {
            method: str(masks.get(method, "") or "")
            for method in METHOD_LABELS
        },
        "cloud_ratios": ratios,
        "ratio_state": ratio_state,
        "difference": float(scene.difference or 0.0),
        "difference_percent": float(scene.difference_percent or 0.0),
        "iou": float(scene.iou or 0.0),
    }


class WebReviewService:
    def __init__(self, config_path: Path, state_path: Path):
        self.config_path = config_path.resolve()
        self.state_path = state_path.resolve()
        self.config = AppConfig.load(self.config_path)
        _resolve_config_paths(self.config, self.config_path)
        self.search_config = replace(
            self.config,
            mask_compare_size=min(max(64, int(self.config.mask_compare_size)), 256),
            enable_anomaly_detector=False,
        )
        self.store = ReviewStore(self.state_path)
        self.scenes: dict[str, Scene] = {}
        self.validations: dict[str, tuple[float, list[Path], list[str]]] = {}
        self.ratio_jobs: dict[str, dict[str, object]] = {}
        self.cloud_stat_exports: dict[str, tuple[float, bytes, str]] = {}
        self.active_ratio_job = ""
        self.ratio_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="cloud-ratio"
        )
        self.lock = threading.RLock()
        self.asset_cache = (
            Path(self.config.cache_dir).expanduser().resolve() / "web_assets"
        )
        self.asset_cache.mkdir(parents=True, exist_ok=True)
        self.ratio_cache = self.asset_cache / "ratio_metrics"
        self.ratio_cache.mkdir(parents=True, exist_ok=True)

    def config_payload(self) -> dict[str, object]:
        return {
            "watch_roots": self.config.watch_roots,
            "methods": METHOD_LABELS,
            "version": "1.6.15",
            "capabilities": [
                "manual_load",
                "precheck",
                "four_corner_warp",
                "offline_basemap",
                "mouse_center_zoom",
                "cloud_text_labels",
                "strict_meta_corner_binding",
                "shp_sibling_meta_upgrade",
                "real_shp_polygon_vertices",
                "cloud_mask_color_modes",
                "web_mercator_projection",
                "fixed_disk_array_lookup",
                "async_cloud_ratios",
                "thumbnail_first",
                "mercator_mesh_warp",
                "bounded_web_mercator_world",
                "fixed_toolbar_selection_scroll",
                "independent_mask_text_toggles",
                "final_cloud_ratio",
                "rasterio_mask_assets",
                "optional_cloud_method_label",
                "linked_cloud_display_method",
                "prioritized_mask_asset_queue",
                "retry_failed_mask_assets",
                "visible_mask_source_paths",
                "mask_asset_error_diagnostics",
                "windows_long_unc_asset_access",
                "scene_list_select_all",
                "stable_scene_list_visibility",
                "explicit_combined_and_final_paths",
                "four_method_cloud_statistics",
                "mask_content_cache_invalidation",
                "search_batch_asset_refresh",
                "arcmap_raster_row_orientation",
                "sampled_mask_fingerprint",
                "independent_ratio_errors",
                "cloud_statistics_excel_export",
            ],
        }

    def _ratio_fingerprint(self, scene: Scene) -> str:
        parts = [str(min(max(64, int(self.search_config.mask_compare_size)), 256))]
        masks = _scene_mask_paths(scene, self.config)
        for method in METHOD_LABELS:
            value = masks.get(method, "")
            if not value:
                parts.append(f"{method}:missing")
                continue
            path = Path(value)
            try:
                stat = os.stat(filesystem_path(path))
                content_digest = _file_content_digest(path)
                parts.append(
                    f"{method}:{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{content_digest}"
                )
            except OSError:
                parts.append(f"{method}:{path}:unavailable")
        return hashlib.sha1("|".join(parts).encode("utf-8", "surrogatepass")).hexdigest()

    def _calculate_ratio_metrics(self, scene: Scene) -> dict[str, object]:
        fingerprint = self._ratio_fingerprint(scene)
        cache_path = self.ratio_cache / f"{fingerprint}.json"
        if cache_path.is_file():
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
                if isinstance(cached, dict):
                    return cached
            except (OSError, ValueError):
                pass

        size = min(max(64, int(self.search_config.mask_compare_size)), 256)
        ratios: dict[str, float] = {}
        method_errors: dict[str, str] = {}
        difference = 0.0
        difference_percent = 0.0
        iou = 1.0
        masks = _scene_mask_paths(scene, self.config)
        mask_images: dict[str, Image.Image] = {}
        for method, path in masks.items():
            if method not in METHOD_LABELS:
                continue
            try:
                image = read_mask(path, size=(size, size))
                mask_images[method] = image
                histogram = image.histogram()
                total = image.width * image.height
                ratios[method] = float(histogram[255] / total) if total else 0.0
            except Exception as exc:
                method_errors[method] = f"{type(exc).__name__}: {exc}"
        if "combined" in mask_images and "rdc" in mask_images:
            combined_ratio, rdc_ratio, difference, iou = compare_mask_images(
                mask_images["combined"], mask_images["rdc"]
            )
            ratios.update(combined=combined_ratio, rdc=rdc_ratio)
            difference_percent = difference_percent_from_ratios(
                combined_ratio, rdc_ratio
            )
        if "omnicloudmask" in mask_images:
            for method in ("combined", "rdc"):
                if method not in mask_images:
                    continue
                _omni_ratio, ref_ratio, diff, ref_iou = compare_mask_images(
                    mask_images["omnicloudmask"], mask_images[method]
                )
                ratios[f"omnicloudmask_vs_{method}_difference"] = diff
                ratios[f"omnicloudmask_vs_{method}_iou"] = ref_iou
                ratios[f"{method}_ratio_for_omni_compare"] = ref_ratio
        result: dict[str, object] = {
            "cloud_ratios": ratios,
            "difference": difference,
            "difference_percent": difference_percent,
            "iou": iou,
            "method_errors": method_errors,
        }
        if not method_errors:
            try:
                cache_path.write_text(
                    json.dumps(result, ensure_ascii=False), encoding="utf-8"
                )
            except OSError:
                pass
        return result

    def _start_ratio_job(self, scenes: list[Scene]) -> str:
        with self.lock:
            for job in self.ratio_jobs.values():
                cancel = job.get("cancel")
                if isinstance(cancel, threading.Event):
                    cancel.set()
            job_id = uuid.uuid4().hex
            cancel_event = threading.Event()
            job: dict[str, object] = {
                "cancel": cancel_event,
                "total": len(scenes),
                "completed": 0,
                "updates": [],
                "errors": [],
                "done": not scenes,
            }
            self.ratio_jobs = {job_id: job}
            self.active_ratio_job = job_id

        def run() -> None:
            completed_scenes: list[Scene] = []
            futures = {
                self.ratio_executor.submit(self._calculate_ratio_metrics, scene): scene
                for scene in scenes
            }
            for future in as_completed(futures):
                scene = futures[future]
                if cancel_event.is_set():
                    for pending in futures:
                        pending.cancel()
                    break
                try:
                    metrics = future.result()
                    scene.cloud_ratios.update(metrics["cloud_ratios"])
                    scene.difference = float(metrics["difference"])
                    scene.difference_percent = float(metrics["difference_percent"])
                    scene.iou = float(metrics["iou"])
                    update = {
                        "scene_id": scene.scene_id,
                        "cloud_ratios": dict(scene.cloud_ratios),
                        "difference": scene.difference,
                        "difference_percent": scene.difference_percent,
                        "iou": scene.iou,
                        "ratio_state": (
                            "partial" if metrics.get("method_errors") else "complete"
                        ),
                        "ratio_errors": dict(metrics.get("method_errors") or {}),
                    }
                    completed_scenes.append(scene)
                    with self.lock:
                        job["updates"].append(update)
                        for method, error in (metrics.get("method_errors") or {}).items():
                            job["errors"].append(
                                {
                                    "scene_id": scene.scene_id,
                                    "method": method,
                                    "error": error,
                                }
                            )
                except Exception as exc:
                    with self.lock:
                        job["errors"].append(
                            {"scene_id": scene.scene_id, "error": str(exc)}
                        )
                finally:
                    with self.lock:
                        job["completed"] = int(job["completed"]) + 1
            if completed_scenes and not cancel_event.is_set():
                self.store.upsert_scenes(completed_scenes)
            with self.lock:
                job["done"] = True

        threading.Thread(target=run, name=f"ratio-job-{job_id[:8]}", daemon=True).start()
        return job_id

    def ratio_progress(self, job_id: str, cursor: int = 0) -> dict[str, object]:
        with self.lock:
            job = self.ratio_jobs.get(job_id)
            if job is None:
                raise FileNotFoundError(job_id)
            updates = list(job["updates"])
            return {
                "job_id": job_id,
                "updates": updates[max(0, cursor):],
                "cursor": len(updates),
                "completed": int(job["completed"]),
                "total": int(job["total"]),
                "done": bool(job["done"]),
                "errors": list(job["errors"]),
            }

    def export_cloud_statistics(self, payload: dict[str, object]) -> dict[str, str]:
        requested = payload.get("scene_ids") or []
        if not isinstance(requested, list):
            raise ValueError("scene_ids 格式错误")
        scene_ids = [str(value) for value in requested if str(value) in self.scenes]
        if not scene_ids:
            raise ValueError("当前筛选范围没有可导出的影像")

        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "云量统计"
        sheet.append(
            [
                "序号",
                "影像ID",
                "卫星",
                "日期",
                "计划号",
                "综合云量",
                "RDC云量",
                "Omni云量",
                "最终结果云量",
                "综合-RDC差异",
                "IoU",
                "计算状态",
            ]
        )
        for index, scene_id in enumerate(scene_ids, 1):
            scene = self.scenes[scene_id]
            ratios = scene.cloud_ratios or {}
            available = _scene_mask_paths(scene, self.config)
            if not available:
                calculation_state = "无掩膜"
            elif all(method in ratios for method in available):
                calculation_state = "完成"
            elif any(method in ratios for method in available):
                calculation_state = "部分结果"
            else:
                calculation_state = "无结果"
            sheet.append(
                [
                    index,
                    scene.scene_id,
                    scene.satellite,
                    scene.acquired_date,
                    getattr(scene, "plan_code", ""),
                    ratios.get("combined"),
                    ratios.get("rdc"),
                    ratios.get("omnicloudmask"),
                    ratios.get("final"),
                    scene.difference,
                    scene.iou,
                    calculation_state,
                ]
            )
        for row in sheet.iter_rows(min_row=2, min_col=6, max_col=10):
            for cell in row:
                if cell.value is not None:
                    cell.number_format = "0.0%"
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        widths = {
            "A": 8,
            "B": 72,
            "C": 16,
            "D": 14,
            "E": 18,
            "F": 14,
            "G": 14,
            "H": 14,
            "I": 16,
            "J": 16,
            "K": 12,
            "L": 18,
        }
        for column, width in widths.items():
            sheet.column_dimensions[column].width = width
        buffer = io.BytesIO()
        workbook.save(buffer)
        token = uuid.uuid4().hex
        filename = time.strftime("cloud_review_statistics_%Y%m%d_%H%M%S.xlsx")
        with self.lock:
            cutoff = time.time() - 3600
            self.cloud_stat_exports = {
                key: value
                for key, value in self.cloud_stat_exports.items()
                if value[0] >= cutoff
            }
            self.cloud_stat_exports[token] = (time.time(), buffer.getvalue(), filename)
        return {"token": token, "filename": filename}

    def cloud_statistics_export(self, token: str) -> tuple[bytes, str]:
        with self.lock:
            value = self.cloud_stat_exports.get(token)
        if value is None:
            raise FileNotFoundError(token)
        return value[1], value[2]

    def _remember(self, scenes: list[Scene], make_manual: bool = False) -> None:
        with self.lock:
            for scene in scenes:
                if make_manual and scene.status == "pending":
                    scene.status = "manual"
                self.scenes[scene.scene_id] = scene
        if make_manual and scenes:
            self.store.upsert_scenes(scenes)

    def _repair_moved_scenes(self, scenes: list[Scene]) -> tuple[list[Scene], int]:
        repaired: list[Scene] = []
        repair_count = 0
        for scene in scenes:
            current = scene
            if not path_is_file(scene.image_path):
                found, _missing = search_scene_candidates_by_ids(
                    self.search_config, scene.scene_id, "", 1
                )
                if found:
                    current = found[0]
                    current.status = scene.status
                    current.review_batch = scene.review_batch
                    repair_count += 1
            if self._refresh_xml_geography(current):
                repair_count += 1
            repaired.append(current)
        if repair_count:
            self.store.upsert_scenes(repaired)
        return repaired, repair_count

    def _refresh_xml_geography(self, scene: Scene) -> bool:
        candidates: list[Path] = []
        diagnostics: list[str] = []
        parent_candidates: list[Path] = []
        if scene.metadata_path:
            metadata_path = Path(scene.metadata_path)
            parent_candidates.append(metadata_path.parent)
            if metadata_path.suffix.lower() == ".xml":
                candidates.append(metadata_path)
        for source_value in (
            scene.source_tif_path,
            scene.image_path,
            scene.preview_image_path,
        ):
            if source_value:
                parent_candidates.append(Path(source_value).parent)
        stems = [scene.scene_id]
        if scene.scene_id.upper().endswith("_MSS"):
            stems.append(scene.scene_id[:-4])
        for parent in dict.fromkeys(parent_candidates):
            for stem in stems:
                candidates.append(parent / f"{stem}_meta.xml")
        if candidates:
            diagnostics.append(f"检查meta.xml：{candidates[0]}")
        seen: set[Path] = set()
        for candidate in candidates:
            resolved = candidate.resolve(strict=False)
            if resolved in seen or not path_is_file(candidate):
                continue
            seen.add(resolved)
            try:
                corners, longitude, latitude = read_xml_geography(candidate)
            except (OSError, ET.ParseError) as exc:
                diagnostics.append(f"meta.xml解析失败：{candidate}：{exc}")
                continue
            if len(corners) != 4:
                diagnostics.append(f"meta.xml四角不完整：{candidate}（{len(corners)}/4）")
                continue
            changed = corners != scene.corners or str(candidate) != scene.metadata_path
            scene.corners = corners
            scene.center_lon = longitude
            scene.center_lat = latitude
            scene.metadata_path = str(candidate)
            scene.geometry_diagnostic = f"已读取meta.xml四角坐标：{candidate}"
            scene.geo_cell = geo_cell_from_center(
                longitude, latitude, self.config.geo_grid_degrees
            )
            return changed
        if scene.metadata_path and Path(scene.metadata_path).suffix.lower() == ".shp":
            shp_path = Path(scene.metadata_path)
            if path_is_file(shp_path):
                try:
                    corners, longitude, latitude = read_shp_geography(shp_path)
                except OSError:
                    corners, longitude, latitude = {}, None, None
                if len(corners) == 4:
                    changed = corners != scene.corners
                    scene.corners = corners
                    scene.center_lon = longitude
                    scene.center_lat = latitude
                    scene.geo_cell = geo_cell_from_center(
                        longitude, latitude, self.config.geo_grid_degrees
                    )
                    scene.geometry_diagnostic = (
                        f"未读取到meta.xml；已使用SHP真实多边形顶点：{shp_path}"
                        + ("；" + "；".join(diagnostics) if diagnostics else "")
                    )
                    return changed
        scene.geometry_diagnostic = "；".join(diagnostics) or "未找到可用的meta.xml或SHP空间信息"
        return False

    def search(self, payload: dict[str, object]) -> dict[str, object]:
        mode = str(payload.get("mode") or "queue")
        limit = max(1, min(int(payload.get("limit") or 500), 2000))
        missing: list[str] = []
        if mode == "ids":
            scene_dirs, missing = find_scene_directories_by_ids(
                self.search_config,
                str(payload.get("ids") or ""),
                str(payload.get("path") or ""),
                limit,
            )
            scenes = scenes_from_directories(
                self.search_config, scene_dirs, calculate_ratios=False
            )
        elif mode == "search":
            scene_dirs = find_scene_candidate_directories(
                self.search_config,
                acquired_date=str(payload.get("date") or ""),
                plan_filter=str(payload.get("plan") or ""),
                explicit_path=str(payload.get("path") or ""),
                limit=limit,
            )
            scenes = scenes_from_directories(
                self.search_config, scene_dirs, calculate_ratios=False
            )
        else:
            scenes = self.store.review_queue(
                str(payload.get("strategy") or "difference")
            )[:limit]
            scenes, repaired_count = self._repair_moved_scenes(scenes)
            self._remember(scenes)
        if mode in {"ids", "search"}:
            self._remember(scenes, make_manual=True)
        scene_payloads = [_scene_payload(scene, self.config) for scene in scenes]
        ratio_job_id = self._start_ratio_job(scenes)
        return {
            "scenes": scene_payloads,
            "missing": missing,
            "count": len(scenes),
            "repaired_paths": repaired_count if mode == "queue" else 0,
            "ratio_job_id": ratio_job_id,
            "ratio_state": "running" if scenes else "complete",
        }

    def validate_search(self, payload: dict[str, object]) -> dict[str, object]:
        mode = str(payload.get("mode") or "")
        limit = max(1, min(int(payload.get("limit") or 500), 2000))
        if mode == "ids":
            scene_dirs, missing = find_scene_directories_by_ids(
                self.search_config,
                str(payload.get("ids") or ""),
                str(payload.get("path") or ""),
                limit,
            )
        elif mode == "search":
            scene_dirs = find_scene_candidate_directories(
                self.search_config,
                acquired_date=str(payload.get("date") or ""),
                plan_filter=str(payload.get("plan") or ""),
                explicit_path=str(payload.get("path") or ""),
                limit=limit,
            )
            missing = []
        else:
            raise ValueError("只有组合检索和指定ID检索支持加载前验证")
        token = uuid.uuid4().hex
        now = time.time()
        with self.lock:
            self.validations = {
                key: value
                for key, value in self.validations.items()
                if now - value[0] < 600
            }
            self.validations[token] = (now, scene_dirs, missing)
        return {
            "token": token,
            "count": len(scene_dirs),
            "missing": missing,
            "limited": len(scene_dirs) >= limit,
        }

    def load_validation(self, payload: dict[str, object]) -> dict[str, object]:
        token = str(payload.get("token") or "")
        with self.lock:
            prepared = self.validations.pop(token, None)
        if prepared is None:
            raise ValueError("预检结果已失效，请重新验证")
        _created_at, scene_dirs, missing = prepared
        scenes = scenes_from_directories(
            self.search_config, scene_dirs, calculate_ratios=False
        )
        loaded_ids = {scene.scene_id for scene in scenes}
        failed = [path.name for path in scene_dirs if path.name not in loaded_ids]
        self._remember(scenes, make_manual=True)
        scene_payloads = [_scene_payload(scene, self.config) for scene in scenes]
        ratio_job_id = self._start_ratio_job(scenes)
        return {
            "scenes": scene_payloads,
            "missing": [*missing, *failed],
            "count": len(scenes),
            "ratio_job_id": ratio_job_id,
            "ratio_state": "running" if scenes else "complete",
        }

    def get_scene(self, scene_id: str) -> Scene | None:
        with self.lock:
            scene = self.scenes.get(scene_id)
        if scene is not None:
            return scene
        matches = [
            item
            for item in self.store.review_queue("difference")
            if item.scene_id == scene_id
        ]
        if matches:
            self._remember(matches)
            return matches[0]
        return None

    def decision(self, payload: dict[str, object]) -> dict[str, object]:
        scene_id = str(payload.get("scene_id") or "")
        decision = str(payload.get("decision") or "")
        method = str(payload.get("method") or "")
        scene = self.get_scene(scene_id)
        if scene is None:
            raise ValueError("没有找到该景，请重新加载列表")
        if decision == "skip":
            self.store.mark_skipped(scene_id)
            scene.status = "skipped"
            return {"ok": True, "status": "skipped"}
        if decision != "accept" or method not in METHOD_LABELS:
            raise ValueError("无效的挑图操作")
        mask_path = _scene_mask_paths(scene, self.config).get(method, "")
        if not mask_path or not path_is_file(mask_path):
            raise ValueError(f"本景没有可用的 {METHOD_LABELS[method]} 掩膜")
        copy_to_pending_import(
            scene,
            mask_path,
            method,
            str(payload.get("note") or ""),
            self.config,
            self.store,
            str(payload.get("label_type") or "cloud"),
        )
        scene.status = "staged"
        final_path = _final_mask_path(scene, self.config)
        if final_path:
            ratio_size = min(
                max(64, int(self.search_config.mask_compare_size)), 256
            )
            scene.cloud_ratios["final"] = cloud_ratio(
                final_path,
                size=(ratio_size, ratio_size),
            )
        return {
            "ok": True,
            "status": "staged",
            "available_masks": [
                name for name in METHOD_LABELS if name in _scene_mask_paths(scene, self.config)
            ],
            "mask_paths": {
                name: str(path or "")
                for name, path in _scene_mask_paths(scene, self.config).items()
                if name in METHOD_LABELS
            },
            "cloud_ratios": {
                name: float(scene.cloud_ratios[name])
                for name in METHOD_LABELS
                if name in scene.cloud_ratios
            },
        }

    def _source_for_asset(self, scene: Scene, kind: str) -> Path:
        if kind == "image":
            value = scene.image_path
        else:
            value = _scene_mask_paths(scene, self.config).get(kind, "")
        path = Path(value) if value else Path()
        if not value or not path_is_file(path):
            raise FileNotFoundError(kind)
        return path

    def asset(
        self,
        scene_id: str,
        kind: str,
        size: int = 768,
        color_hex: str = "",
    ) -> tuple[Path, str]:
        scene = self.get_scene(scene_id)
        if scene is None:
            raise FileNotFoundError(scene_id)
        size = max(128, min(int(size), 1024))
        if kind == "image" and size <= 384 and scene.preview_image_path:
            preview = Path(scene.preview_image_path)
            source = preview if path_is_file(preview) else self._source_for_asset(scene, kind)
        else:
            source = self._source_for_asset(scene, kind)
        stat = os.stat(filesystem_path(source))
        normalized_color = color_hex.strip().lstrip("#").lower()
        if not re.fullmatch(r"[0-9a-f]{6}", normalized_color):
            normalized_color = ""
        content_digest = "" if kind == "image" else _file_content_digest(source)
        fingerprint = hashlib.sha1(
            f"{source.resolve()}|{stat.st_mtime_ns}|{stat.st_size}|{content_digest}|{kind}|{size}|{normalized_color}".encode(
                "utf-8", "surrogatepass"
            )
        ).hexdigest()
        suffix = ".jpg" if kind == "image" else ".png"
        target = self.asset_cache / f"{fingerprint}{suffix}"
        if target.is_file():
            return target, "image/jpeg" if suffix == ".jpg" else "image/png"
        if kind == "image":
            with open_image_file(source) as opened:
                image = opened.copy()
            image.thumbnail((size, size), Image.Resampling.BILINEAR)
            if image.mode != "RGB":
                image = image.convert("RGB")
            image.save(target, "JPEG", quality=80, optimize=False)
            return target, "image/jpeg"
        mask = read_mask(str(source), size=(size, size))
        color = (
            tuple(int(normalized_color[index : index + 2], 16) for index in (0, 2, 4))
            if normalized_color
            else MASK_COLORS.get(kind, MASK_COLORS["final"])
        )
        alpha = mask.point(lambda value: 178 if value else 0)
        overlay = Image.new("RGBA", mask.size, (*color, 0))
        overlay.putalpha(alpha)
        overlay.save(target, "PNG", compress_level=1)
        return target, "image/png"

    def asset_diagnostic(
        self,
        scene_id: str,
        kind: str,
        size: int = 768,
        color_hex: str = "",
    ) -> dict[str, object]:
        scene = self.get_scene(scene_id)
        if scene is None:
            return {"ok": False, "source_path": "", "error": f"场景不存在：{scene_id}"}
        masks = _scene_mask_paths(scene, self.config)
        source_path = (
            scene.image_path
            if kind == "image"
            else masks.get(kind, "") or str(scene.masks.get(kind, "") or "")
        )
        try:
            target, content_type = self.asset(scene_id, kind, size, color_hex)
            return {
                "ok": True,
                "source_path": str(source_path or ""),
                "cache_path": str(target),
                "content_type": content_type,
            }
        except Exception as exc:
            return {
                "ok": False,
                "source_path": str(source_path or ""),
                "error": f"{type(exc).__name__}: {exc}",
            }


class WebReviewHandler(BaseHTTPRequestHandler):
    service: WebReviewService

    def log_message(self, format: str, *args: object) -> None:
        print(f"[Web] {self.address_string()} - {format % args}")

    def _json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 2 * 1024 * 1024:
            raise ValueError("请求内容过大")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("请求格式错误")
        return value

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            payload = self._read_json()
            if route == "/api/search":
                self._json(self.service.search(payload))
            elif route == "/api/validate":
                self._json(self.service.validate_search(payload))
            elif route == "/api/load-validation":
                self._json(self.service.load_validation(payload))
            elif route == "/api/decision":
                self._json(self.service.decision(payload))
            elif route == "/api/export-cloud-stats":
                self._json(self.service.export_cloud_statistics(payload))
            else:
                self._json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
        except (ValueError, FileNotFoundError) as exc:
            self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            self._json({"error": f"服务器处理失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            self._json(self.service.config_payload())
            return
        if parsed.path == "/api/ratio-progress":
            query = parse_qs(parsed.query)
            try:
                job_id = str(query.get("job", [""])[0])
                cursor = int(query.get("cursor", ["0"])[0])
                self._json(self.service.ratio_progress(job_id, cursor))
            except (ValueError, FileNotFoundError):
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/download-cloud-stats":
            query = parse_qs(parsed.query)
            try:
                data, filename = self.service.cloud_statistics_export(
                    str(query.get("token", [""])[0])
                )
                self.send_response(HTTPStatus.OK)
                self.send_header(
                    "Content-Type",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
                self.send_header(
                    "Content-Disposition", f'attachment; filename="{filename}"'
                )
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except FileNotFoundError:
                self.send_error(HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/api/asset-diagnostic":
            query = parse_qs(parsed.query)
            scene_id = str(query.get("scene", [""])[0])
            kind = str(query.get("kind", ["image"])[0])
            try:
                size = int(query.get("size", ["768"])[0])
            except ValueError:
                size = 768
            color_hex = str(query.get("color", [""])[0])
            self._json(self.service.asset_diagnostic(scene_id, kind, size, color_hex))
            return
        if parsed.path == "/api/asset":
            query = parse_qs(parsed.query)
            try:
                scene_id = str(query.get("scene", [""])[0])
                kind = str(query.get("kind", ["image"])[0])
                size = int(query.get("size", ["768"])[0])
                color_hex = str(query.get("color", [""])[0])
                target, content_type = self.service.asset(
                    scene_id, kind, size, color_hex
                )
                data = target.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "private, no-cache")
                self.end_headers()
                self.wfile.write(data)
            except (ValueError, FileNotFoundError, OSError) as exc:
                self._json(
                    {"error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.NOT_FOUND,
                )
            except Exception as exc:
                self._json(
                    {"error": f"{type(exc).__name__}: {exc}"},
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return
        relative = "index.html" if parsed.path in {"", "/"} else parsed.path.lstrip("/")
        target = (STATIC_ROOT / relative).resolve()
        try:
            target.relative_to(STATIC_ROOT.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)


def create_server(
    host: str,
    port: int,
    config_path: Path = DEFAULT_CONFIG_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
) -> ThreadingHTTPServer:
    service = WebReviewService(config_path, state_path)
    handler = type(
        "ConfiguredWebReviewHandler",
        (WebReviewHandler,),
        {"service": service},
    )
    return ThreadingHTTPServer((host, port), handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="云查看与标注 Web 端")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH))
    parser.add_argument("--state", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    server = create_server(
        args.host,
        args.port,
        Path(args.config),
        Path(args.state),
    )
    url = f"http://127.0.0.1:{args.port}/"
    print(f"云查看 Web 端已启动：{url}")
    print("按 Ctrl+C 可停止服务。")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
