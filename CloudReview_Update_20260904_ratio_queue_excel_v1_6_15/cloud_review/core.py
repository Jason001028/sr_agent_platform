from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import pymysql
from pymysql.cursors import DictCursor
from PIL import Image, ImageChops, ImageFilter, ImageStat

Image.MAX_IMAGE_PIXELS = None

try:
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
except Exception:
    np = None
    rasterio = None
    Resampling = None


MASK_SUFFIXES = {
    "combined": (
        "_Combined_Cloud.tif",
    ),
    "rdc": (
        "_L1_MSS_RDC_Cloud.tif",
        "_RDC_Cloud.tif",
        "_rdc_cloud_binary.tif",
    ),
    "omnicloudmask": (
        "_omnicloudmask_cloud_binary.tif",
        "_omnicloudmask_mask.tif",
    ),
}
GENERIC_CLOUD_SUFFIXES = (
    "_L1_MSS_Cloud.tif",
    "_L1_MSS_cloud.tif",
    "_L1_MSS_cloud.TIF",
)
PREVIEW_SUFFIXES = (
    "_L1_MSS.jpg",
    "_L1_MSS.jpeg",
)
SOURCE_TIF_SUFFIXES = (
    "_L1_MSS.tif",
    "_L1_MSS.tiff",
)
META_SUFFIXES = ("_L1_MSS_meta.xml", "_meta.xml")
SHP_SUFFIXES = ("_L1_MSS.shp", ".shp")
CORNER_NAMES = ("upper_left", "upper_right", "lower_right", "lower_left")
LABEL_VALUES = {
    "cloud": 1,
    "snow": 2,
    "other": 3,
}
LABEL_NAMES = {
    "cloud": "云",
    "snow": "雪",
    "other": "其他",
}
CLOUD_AMOUNT_METHODS = ("combined", "rdc", "omnicloudmask")


def _strip_windows_long_path(value: str) -> str:
    if value.startswith("\\\\?\\UNC\\"):
        return "\\\\" + value[8:]
    if value.startswith("\\\\?\\"):
        return value[4:]
    return value


def _windows_long_path(path: Path) -> str:
    value = str(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    absolute = os.path.abspath(value)
    if absolute.startswith("\\\\"):
        return "\\\\?\\UNC\\" + absolute.lstrip("\\")
    return "\\\\?\\" + absolute


def _display_path(path: Path | str) -> str:
    value = _strip_windows_long_path(str(path))
    try:
        return _strip_windows_long_path(str(Path(value).resolve(strict=False)))
    except (OSError, ValueError):
        return value


def filesystem_path(path: Path | str) -> str:
    return _windows_long_path(Path(str(path)))


def path_exists(path: Path | str) -> bool:
    return _path_exists(Path(str(path)))


def path_is_file(path: Path | str) -> bool:
    return _path_is_file(Path(str(path)))


def path_is_dir(path: Path | str) -> bool:
    return _path_is_dir(Path(str(path)))


def open_image_file(path: Path | str) -> Image.Image:
    return Image.open(filesystem_path(path))


def _path_exists(path: Path) -> bool:
    try:
        if path.exists():
            return True
    except (OSError, ValueError):
        pass
    if os.name == "nt":
        try:
            return os.path.exists(_windows_long_path(path))
        except (OSError, ValueError):
            return False
    return False


def _path_is_file(path: Path) -> bool:
    try:
        if path.is_file():
            return True
    except (OSError, ValueError):
        pass
    if os.name == "nt":
        try:
            return os.path.isfile(_windows_long_path(path))
        except (OSError, ValueError):
            return False
    return False


def _path_is_dir(path: Path) -> bool:
    try:
        if path.is_dir():
            return True
    except (OSError, ValueError):
        pass
    if os.name == "nt":
        try:
            return os.path.isdir(_windows_long_path(path))
        except (OSError, ValueError):
            return False
    return False


def _iter_child_paths(directory: Path) -> Iterable[Path]:
    try:
        yield from directory.iterdir()
        return
    except (OSError, ValueError):
        pass
    if os.name != "nt":
        return
    try:
        with os.scandir(_windows_long_path(directory)) as iterator:
            for entry in iterator:
                yield Path(_strip_windows_long_path(entry.path))
    except (OSError, ValueError):
        return


def normalize_label_type(label_type: str | None) -> str:
    key = (label_type or "cloud").strip().lower()
    return key if key in LABEL_VALUES else "other"


def label_value(label_type: str | None) -> int:
    return LABEL_VALUES[normalize_label_type(label_type)]


def label_name(label_type: str | None) -> str:
    return LABEL_NAMES[normalize_label_type(label_type)]


def _scene_cloud_ratio(scene: "Scene", method: str) -> float | None:
    value = scene.cloud_ratios.get(method)
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(ratio):
        return None
    return ratio


def filter_scenes_by_cloud_amount_order(
    scenes: Sequence["Scene"],
    ordered_methods: Sequence[str],
) -> list["Scene"]:
    methods = tuple(ordered_methods)
    if sorted(methods) != sorted(CLOUD_AMOUNT_METHODS) or len(methods) != len(
        CLOUD_AMOUNT_METHODS
    ):
        raise ValueError("cloud amount order must contain combined, rdc and omnicloudmask")

    def ratios_for(scene: "Scene") -> tuple[float, float, float] | None:
        ratios = tuple(_scene_cloud_ratio(scene, method) for method in methods)
        if any(value is None for value in ratios):
            return None
        return ratios  # type: ignore[return-value]

    matched: list[tuple[tuple[float, float, float], Scene]] = []
    for scene in scenes:
        ratios = ratios_for(scene)
        if ratios is None:
            continue
        if all(left >= right for left, right in zip(ratios, ratios[1:])):
            matched.append((ratios, scene))

    matched.sort(
        key=lambda item: (
            -item[0][0],
            -item[0][1],
            -item[0][2],
            item[1].acquired_date,
            item[1].scene_id,
        )
    )
    return [scene for _ratios, scene in matched]


def materialize_label_mask(
    mask_path: str | Path,
    label_type: str | None,
    target_path: str | Path,
) -> None:
    selected_value = label_value(label_type)
    known_values = set(LABEL_VALUES.values())
    lookup = [
        0 if value == 0 else value if value in known_values else selected_value
        for value in range(256)
    ]
    with open_image_file(mask_path) as source:
        mask = source.convert("L").point(lookup)
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mask.save(target, compression="tiff_lzw")


def fixed_square_size(config: "AppConfig") -> tuple[int, int]:
    size = max(1, int(getattr(config, "review_cache_size", 512) or 512))
    return size, size


def save_resampled_image(
    source_path: str | Path,
    target_path: str | Path,
    size: tuple[int, int],
) -> None:
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open_image_file(source_path) as source:
        image = source.convert("RGB").resize(size, Image.Resampling.BILINEAR)
    suffix = target.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        image.save(target, quality=92)
    else:
        image.save(target, compression="tiff_lzw")


def save_resampled_mask(
    source_path: str | Path,
    target_path: str | Path,
    size: tuple[int, int],
    label_type: str | None = None,
    materialize_label: bool = False,
    mask_encoding: str | None = None,
) -> None:
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = normalize_dataset_mask_encoding(mask_encoding) if mask_encoding else ""
    if mode == "binary255":
        mask = read_mask(str(source_path), size=size)
    else:
        with open_image_file(source_path) as source:
            mask = source.convert("L")
            if mask.size != size:
                mask = mask.resize(size, Image.Resampling.NEAREST)
    if materialize_label:
        if mode == "binary255":
            mask = mask.point(lambda value: 255 if value else 0)
        else:
            selected_value = label_value(label_type)
            known_values = set(LABEL_VALUES.values())
            lookup = [
                0 if value == 0 else value if value in known_values else selected_value
                for value in range(256)
            ]
            mask = mask.point(lookup)
    mask.save(target, compression="tiff_lzw")


@dataclass
class AppConfig:
    watch_roots: list[str] = field(default_factory=lambda: ["demo_disk_array"])
    satellite_filters: list[str] = field(default_factory=lambda: ["JL1"])
    database_dir: str = "review_database"
    mysql_host: str = "localhost"
    mysql_port: int = 3307
    mysql_user: str = "root"
    mysql_password: str = "123456"
    mysql_database: str = "cloud"
    mysql_table: str = "path"
    dataset_output_dir: str = "review_dataset"
    dataset_mask_encoding: str = "binary255"
    annotation_types: list[str] = field(
        default_factory=lambda: ["cloud", "snow", "other"]
    )
    default_annotation_type: str = "cloud"
    typical_dir: str = "typical_dataset"
    cache_dir: str = ".review_cache"
    scan_log_path: str = ""
    scan_interval_seconds: int = 300
    daily_review_limit: int = 50
    queue_strategy: str = "difference"
    minimum_difference: float = 0.05
    enable_difference_delta: bool = True
    enable_difference_percent: bool = False
    minimum_difference_percent: float = 0.6
    minimum_difference_for_percent: float = 0.001
    review_cache_size: int = 512
    mask_compare_size: int = 512
    validation_ratio: float = 0.25
    cache_retention_days: int = 7
    cache_max_mb: int = 512
    omnicloudmask_model_dir: str = "models/omnicloudmask"
    omnicloudmask_device: str = "cpu"
    omnicloudmask_python: str = ""
    omnicloudmask_downsample: int = 1
    auto_run_omnicloudmask_for_review: bool = True
    auto_omni_start_hour: int = 20
    auto_omni_end_hour: int = 8
    scan_lookback_days: int = 14
    scan_max_new_scenes: int = 2000
    scan_max_directories: int = 100000
    scan_workers: int = 4
    geo_grid_degrees: float = 5.0
    enable_anomaly_detector: bool = False
    anomaly_score_threshold: float = 0.58

    @classmethod
    def load(cls, path: Path) -> "AppConfig":
        if not path.exists():
            config = cls()
            config.save(path)
            return config
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        allowed = cls.__dataclass_fields__.keys()
        return cls(**{key: value for key, value in payload.items() if key in allowed})

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


@dataclass
class Scene:
    scene_id: str
    satellite: str
    acquired_date: str
    image_path: str
    source_tif_path: str
    masks: dict[str, str]
    plan_code: str = ""
    metadata_path: str = ""
    corners: dict[str, list[float]] = field(default_factory=dict)
    center_lon: float | None = None
    center_lat: float | None = None
    month: int = 0
    season: str = ""
    geo_cell: str = "unknown"
    cloud_ratios: dict[str, float] = field(default_factory=dict)
    anomaly_score: float = 0.0
    anomaly_reason: str = ""
    difference: float = 0.0
    difference_percent: float = 0.0
    iou: float = 1.0
    status: str = "pending"
    review_batch: str = ""
    preview_image_path: str = ""
    geometry_diagnostic: str = ""


@dataclass
class ScanGroup:
    group_date: str
    satellite: str
    path: str
    status: str = "pending"
    total_scenes: int = 0
    review_scenes: int = 0
    checked_scenes: int = 0


class MySQLDatasetStore:
    def __init__(self, config: AppConfig):
        self.host = config.mysql_host
        self.port = int(config.mysql_port)
        self.user = config.mysql_user
        self.password = config.mysql_password
        self.database = config.mysql_database
        self.table = config.mysql_table
        if not self.database.replace("_", "").isalnum():
            raise ValueError("MySQL 数据库名只能包含字母、数字和下划线")
        if not self.table.replace("_", "").isalnum():
            raise ValueError("MySQL 表名只能包含字母、数字和下划线")

    def connect(self, include_database: bool = True):
        parameters = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "connect_timeout": 5,
            "read_timeout": 15,
            "write_timeout": 15,
            "autocommit": False,
        }
        if include_database:
            parameters["database"] = self.database
        return pymysql.connect(**parameters)

    def table_columns(self, cursor) -> set[str]:
        cursor.execute(f"SHOW COLUMNS FROM `{self.table}`")
        return {row["Field"] for row in cursor.fetchall()}

    @staticmethod
    def corner_column_values(scene: Scene) -> dict[str, float | None]:
        values: dict[str, float | None] = {}
        for corner_name in CORNER_NAMES:
            point = scene.corners.get(corner_name)
            lon: float | None = None
            lat: float | None = None
            if point and len(point) >= 2:
                lon = float(point[0])
                lat = float(point[1])
            values[f"{corner_name}_lon"] = lon
            values[f"{corner_name}_lat"] = lat
            values[f"{corner_name}_longitude"] = lon
            values[f"{corner_name}_latitude"] = lat
        return values

    def ensure_schema(self) -> None:
        with self.connect(include_database=False) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.database}` "
                    "DEFAULT CHARACTER SET utf8mb4"
                )
            connection.commit()
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS `{self.table}` (
                        `id` INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
                        `sat` VARCHAR(255) NOT NULL,
                        `tra_val` CHAR(10) DEFAULT NULL,
                        `label_path` TEXT DEFAULT NULL,
                        `jpg_path` TEXT DEFAULT NULL,
                        `image_id` VARCHAR(255) DEFAULT NULL,
                        `source_image_path` TEXT DEFAULT NULL,
                        `source_method` VARCHAR(100) DEFAULT NULL,
                        `center_lon` DOUBLE DEFAULT NULL,
                        `center_lat` DOUBLE DEFAULT NULL,
                        `upper_left_lon` DOUBLE DEFAULT NULL,
                        `upper_left_lat` DOUBLE DEFAULT NULL,
                        `upper_right_lon` DOUBLE DEFAULT NULL,
                        `upper_right_lat` DOUBLE DEFAULT NULL,
                        `lower_right_lon` DOUBLE DEFAULT NULL,
                        `lower_right_lat` DOUBLE DEFAULT NULL,
                        `lower_left_lon` DOUBLE DEFAULT NULL,
                        `lower_left_lat` DOUBLE DEFAULT NULL,
                        `month` TINYINT UNSIGNED DEFAULT NULL,
                        `season` VARCHAR(20) DEFAULT NULL,
                        `geo_cell` VARCHAR(100) DEFAULT NULL,
                        `corners_json` TEXT DEFAULT NULL,
                        `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                            ON UPDATE CURRENT_TIMESTAMP,
                        INDEX (`tra_val`),
                        INDEX (`image_id`)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                    """
                )
                existing = self.table_columns(cursor)
                if {"id", "jpg_path", "image_id"}.issubset(existing):
                    cursor.execute(
                        f"""
                        SELECT id, jpg_path FROM `{self.table}`
                        WHERE (image_id IS NULL OR image_id='')
                          AND jpg_path IS NOT NULL AND jpg_path<>''
                        """
                    )
                    for row in cursor.fetchall():
                        filename = (
                            str(row["jpg_path"]).replace("\\", "/").rsplit("/", 1)[-1]
                        )
                        image_id = os.path.splitext(filename)[0]
                        if image_id:
                            cursor.execute(
                                f"UPDATE `{self.table}` SET image_id=%s WHERE id=%s",
                                (image_id, row["id"]),
                            )
            connection.commit()

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.ensure_schema()
            with self.connect() as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT VERSION() AS version")
                    version = cursor.fetchone()["version"]
            return True, f"MySQL {version} / {self.database}.{self.table}"
        except Exception as exc:
            return False, str(exc)

    def add_record(
        self,
        scene: Scene,
        dataset_split: str,
        image_path: str,
        mask_path: str,
        method: str,
    ) -> None:
        with self.connect() as connection:
            try:
                with connection.cursor() as cursor:
                    existing_columns = self.table_columns(cursor)
                    lookup_sql = ""
                    lookup_values: tuple[object, ...] = ()
                    if {"id", "image_id", "jpg_path"}.issubset(existing_columns):
                        lookup_sql = "WHERE image_id=%s OR jpg_path=%s"
                        lookup_values = (scene.scene_id, image_path)
                    elif {"id", "jpg_path"}.issubset(existing_columns):
                        lookup_sql = "WHERE jpg_path=%s"
                        lookup_values = (image_path,)
                    existing = None
                    if lookup_sql:
                        cursor.execute(
                            f"""
                            SELECT id FROM `{self.table}`
                            {lookup_sql}
                            LIMIT 1
                            """,
                            lookup_values,
                        )
                        existing = cursor.fetchone()
                    values_by_column: dict[str, object] = {
                        "sat": scene.satellite,
                        "tra_val": dataset_split,
                        "label_path": mask_path,
                        "jpg_path": image_path,
                        "image_id": scene.scene_id,
                        "source_image_path": scene.image_path,
                        "source_method": method,
                        "center_lon": scene.center_lon,
                        "center_lat": scene.center_lat,
                        "month": scene.month or None,
                        "season": scene.season,
                        "geo_cell": scene.geo_cell,
                        "corners_json": json.dumps(scene.corners, ensure_ascii=False),
                    }
                    values_by_column.update(self.corner_column_values(scene))
                    writable_values = {
                        column: value
                        for column, value in values_by_column.items()
                        if column in existing_columns
                    }
                    if not writable_values:
                        raise RuntimeError(
                            f"MySQL 表 `{self.table}` 没有可写入的数据集字段"
                        )
                    if existing:
                        assignments = ", ".join(
                            f"`{column}`=%s" for column in writable_values
                        )
                        cursor.execute(
                            f"UPDATE `{self.table}` SET {assignments} WHERE id=%s",
                            tuple(writable_values.values()) + (existing["id"],),
                        )
                    else:
                        columns_sql = ", ".join(
                            f"`{column}`" for column in writable_values
                        )
                        placeholders = ", ".join(["%s"] * len(writable_values))
                        cursor.execute(
                            f"""
                            INSERT INTO `{self.table}` ({columns_sql})
                            VALUES ({placeholders})
                            """,
                            tuple(writable_values.values()),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def split_counts(self) -> dict[str, int]:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT tra_val, COUNT(*) AS count
                    FROM `{self.table}` GROUP BY tra_val
                    """
                )
                rows = cursor.fetchall()
        return {
            str(row["tra_val"] or "unknown"): int(row["count"])
            for row in rows
        }

    def scene_ids(self) -> set[str]:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                existing_columns = self.table_columns(cursor)
                if "image_id" in existing_columns:
                    cursor.execute(
                        f"""
                        SELECT image_id FROM `{self.table}`
                        WHERE image_id IS NOT NULL AND image_id<>''
                        """
                    )
                    return {str(row["image_id"]) for row in cursor.fetchall()}
                if "jpg_path" in existing_columns:
                    cursor.execute(
                        f"""
                        SELECT jpg_path FROM `{self.table}`
                        WHERE jpg_path IS NOT NULL AND jpg_path<>''
                        """
                    )
                    ids: set[str] = set()
                    for row in cursor.fetchall():
                        filename = (
                            str(row["jpg_path"]).replace("\\", "/").rsplit("/", 1)[-1]
                        )
                        image_id = os.path.splitext(filename)[0]
                        if image_id:
                            ids.add(image_id)
                    return ids
        return set()

    def dataset_records(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            with connection.cursor() as cursor:
                existing_columns = self.table_columns(cursor)
                required = {"sat", "tra_val", "label_path", "jpg_path"}
                missing = sorted(required - existing_columns)
                if missing:
                    raise RuntimeError(
                        f"MySQL 表 `{self.table}` 缺少必要字段：{', '.join(missing)}"
                    )
                optional = [
                    "id",
                    "image_id",
                    "source_image_path",
                    "source_method",
                    "center_lon",
                    "center_lat",
                    "month",
                    "season",
                    "geo_cell",
                    "corners_json",
                    "upper_left_lon",
                    "upper_left_lat",
                    "upper_left_longitude",
                    "upper_left_latitude",
                    "upper_right_lon",
                    "upper_right_lat",
                    "upper_right_longitude",
                    "upper_right_latitude",
                    "lower_right_lon",
                    "lower_right_lat",
                    "lower_right_longitude",
                    "lower_right_latitude",
                    "lower_left_lon",
                    "lower_left_lat",
                    "lower_left_longitude",
                    "lower_left_latitude",
                ]
                columns = [
                    column
                    for column in ["sat", "tra_val", "label_path", "jpg_path", *optional]
                    if column in existing_columns
                ]
                columns_sql = ", ".join(f"`{column}`" for column in columns)
                cursor.execute(
                    f"""
                    SELECT {columns_sql} FROM `{self.table}`
                    WHERE label_path IS NOT NULL AND label_path<>''
                      AND jpg_path IS NOT NULL AND jpg_path<>''
                    ORDER BY id
                    """
                    if "id" in existing_columns
                    else f"""
                    SELECT {columns_sql} FROM `{self.table}`
                    WHERE label_path IS NOT NULL AND label_path<>''
                      AND jpg_path IS NOT NULL AND jpg_path<>''
                    """
                )
                return [dict(row) for row in cursor.fetchall()]


def normalize_mask(
    image: Image.Image, size: tuple[int, int] | None = None
) -> Image.Image:
    if size and image.size != size:
        image = image.resize(size, Image.Resampling.NEAREST)
    return image.convert("L").point(lambda value: 255 if value else 0)


def _downsample_size(
    size: tuple[int, int], max_pixels: int
) -> tuple[int, int]:
    width, height = size
    if max_pixels <= 0 or width * height <= max_pixels:
        return size
    scale = (max_pixels / float(width * height)) ** 0.5
    return max(1, int(width * scale)), max(1, int(height * scale))


def image_size(path: str | Path) -> tuple[int, int]:
    with open_image_file(path) as image:
        return image.size


def _read_mask_with_rasterio(
    path: str | Path, size: tuple[int, int]
) -> Image.Image | None:
    if rasterio is None or np is None or Resampling is None:
        return None
    try:
        with rasterio.open(filesystem_path(path)) as dataset:
            array = dataset.read(
                1,
                out_shape=(size[1], size[0]),
                resampling=Resampling.nearest,
            )
        mask = (array != 0).astype("uint8") * 255
        return Image.fromarray(mask, mode="L")
    except Exception:
        return None


def read_mask(
    path: str,
    size: tuple[int, int] | None = None,
    max_pixels: int = 0,
) -> Image.Image:
    if size is None and max_pixels > 0:
        size = _downsample_size(image_size(path), max_pixels)
    if size is not None:
        raster_mask = _read_mask_with_rasterio(path, size)
        if raster_mask is not None:
            return raster_mask
    with open_image_file(path) as image:
        return normalize_mask(image, size)


def _white_ratio(mask: Image.Image) -> float:
    histogram = mask.histogram()
    total = mask.width * mask.height
    return float(histogram[255] / total) if total else 0.0


def compare_masks(
    first_path: str,
    second_path: str,
    max_pixels: int = 0,
    target_size: tuple[int, int] | None = None,
) -> tuple[float, float, float, float]:
    if target_size is None and max_pixels > 0:
        target_size = _downsample_size(image_size(first_path), max_pixels)
    return compare_mask_images(
        read_mask(first_path, target_size),
        read_mask(second_path, target_size),
    )


def compare_mask_images(
    first: Image.Image, second: Image.Image
) -> tuple[float, float, float, float]:
    first = normalize_mask(first)
    second = normalize_mask(second, first.size)
    first_ratio = _white_ratio(first)
    second_ratio = _white_ratio(second)
    difference = _white_ratio(ImageChops.logical_xor(first.convert("1"), second.convert("1")).convert("L"))
    intersection = _white_ratio(
        ImageChops.logical_and(first.convert("1"), second.convert("1")).convert("L")
    )
    union = _white_ratio(
        ImageChops.logical_or(first.convert("1"), second.convert("1")).convert("L")
    )
    iou = float(intersection / union) if union else 1.0
    return first_ratio, second_ratio, difference, iou


def difference_percent_from_ratios(first_ratio: float, second_ratio: float) -> float:
    difference = abs(first_ratio - second_ratio)
    if difference <= 0:
        return 0.0
    denominator = min(abs(first_ratio), abs(second_ratio))
    if denominator <= 1e-9:
        return 1.0
    return difference / denominator


def scene_matches_difference_rules(scene: "Scene", config: AppConfig) -> bool:
    checks: list[bool] = []
    if config.enable_difference_delta:
        checks.append(scene.difference >= config.minimum_difference)
    if config.enable_difference_percent:
        checks.append(
            scene.difference >= config.minimum_difference_for_percent
            and scene.difference_percent >= config.minimum_difference_percent
        )
    return any(checks)


def cloud_ratio(path: str, size: tuple[int, int] | None = None) -> float:
    return _white_ratio(read_mask(path, size=size))


def run_omnicloudmask_for_scene(scene: "Scene", config: AppConfig) -> str:
    source_tif = Path(scene.source_tif_path)
    if not path_exists(source_tif):
        raise RuntimeError(f"缺少四波段 MSS.tif：{source_tif}")
    output_root = Path(config.cache_dir).expanduser().resolve() / "omnicloudmask"
    output_root.mkdir(parents=True, exist_ok=True)
    result = (
        output_root
        / source_tif.stem
        / f"{source_tif.stem}_omnicloudmask_cloud_binary.tif"
    )
    if result.exists():
        return str(result)
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_omnicloudmask_jilin1.py"
    python_exe = (config.omnicloudmask_python or "").strip() or sys.executable
    command = [
        python_exe,
        str(script),
        "--input-file",
        filesystem_path(source_tif),
        "--out",
        str(output_root),
        "--model-dir",
        str(Path(config.omnicloudmask_model_dir).expanduser()),
        "--device",
        config.omnicloudmask_device,
        "--downsample",
        str(max(1, int(config.omnicloudmask_downsample or 1))),
    ]
    completed = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=7200,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(details[-3000:] or "OmniCloudMask 运行失败")
    if not result.exists():
        raise RuntimeError(f"OmniCloudMask 已结束但未找到结果：{result}")
    return str(result)


def attach_omnicloudmask_result(scene: "Scene", mask_path: str, config: AppConfig) -> None:
    scene.masks["omnicloudmask"] = mask_path
    compare_size = max(1, int(config.mask_compare_size or 512))
    scene.cloud_ratios["omnicloudmask"] = cloud_ratio(
        mask_path, size=(compare_size, compare_size)
    )
    scene.cloud_ratios["omnicloudmask_downsample"] = float(
        max(1, int(config.omnicloudmask_downsample or 1))
    )
    for method in ("combined", "rdc"):
        reference = scene.masks.get(method)
        if not reference or not path_exists(reference):
            continue
        _omni_ratio, ref_ratio, diff, iou = compare_masks(
            mask_path,
            reference,
            target_size=(compare_size, compare_size),
        )
        scene.cloud_ratios[f"omnicloudmask_vs_{method}_difference"] = diff
        scene.cloud_ratios[f"omnicloudmask_vs_{method}_iou"] = iou
        scene.cloud_ratios[f"{method}_ratio_for_omni_compare"] = ref_ratio


def attach_existing_omnicloudmask_result(scene: "Scene", config: AppConfig) -> bool:
    if "omnicloudmask" in scene.masks and path_exists(scene.masks["omnicloudmask"]):
        return True
    for source in (scene.source_tif_path, scene.image_path):
        if not source:
            continue
        scene_dir = Path(source).parent
        if scene_dir.name != scene.scene_id:
            continue
        mask_path, _checked, _candidates = _find_omnicloudmask_cloud(
            scene_dir,
            scene.scene_id,
        )
        if mask_path and path_exists(mask_path):
            attach_omnicloudmask_result(scene, mask_path, config)
            return True
    return False


def _find_cached_omnicloudmask_path(cache_path: str | Path) -> str | None:
    folder = Path(cache_path)
    if not _path_is_dir(folder):
        return None
    preferred = (
        folder / "omnicloudmask.tif",
        folder / "omnicloudmask.tiff",
        folder / "omnicloudmask.TIF",
        folder / "omnicloudmask.TIFF",
    )
    found = _find_first_existing(preferred)
    if found:
        return found
    matches = sorted(
        [
            path
            for path in _iter_child_paths(folder)
            if _path_is_file(path)
            and path.suffix.lower() in {".tif", ".tiff"}
            and path.stem.lower().startswith("omnicloudmask")
        ],
        key=lambda path: path.name.lower(),
    )
    return _display_path(matches[0]) if matches else None


def scene_has_cached_omnicloudmask(scene: "Scene") -> bool:
    cache_path = str(scene.cloud_ratios.get("review_cache_path") or "")
    return bool(cache_path and _find_cached_omnicloudmask_path(cache_path))


def attach_cached_omnicloudmask_result(scene: "Scene", config: AppConfig) -> bool:
    cache_path = str(scene.cloud_ratios.get("review_cache_path") or "")
    if not cache_path:
        return False
    mask_path = _find_cached_omnicloudmask_path(cache_path)
    if not mask_path:
        return False
    attach_omnicloudmask_result(scene, mask_path, config)
    return True


def auto_omnicloudmask_allowed(
    config: AppConfig, when: datetime | None = None
) -> bool:
    current = when or datetime.now()
    start = int(config.auto_omni_start_hour) % 24
    end = int(config.auto_omni_end_hour) % 24
    hour = current.hour
    if start == end:
        return True
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def _strip_known_suffix(name: str, suffixes: Iterable[str]) -> str | None:
    lowered = name.lower()
    for suffix in suffixes:
        if lowered.endswith(suffix.lower()):
            return name[: -len(suffix)]
    return None


def identify_file(filename: str) -> tuple[str, str] | None:
    lowered = filename.lower()
    for suffix in PREVIEW_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            return Path(filename).stem, "image"
    for suffix in SOURCE_TIF_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            return Path(filename).stem, "source_tif"
    for suffix in META_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            scene_id = filename[: -len(suffix)]
            if suffix.lower().startswith("_l1_mss"):
                scene_id += "_L1_MSS"
            return scene_id, "meta"
    if lowered.endswith(".shp"):
        scene_id = filename[:-4]
        return scene_id, "shp"
    for mask_kind, suffixes in MASK_SUFFIXES.items():
        for suffix in suffixes:
            if not lowered.endswith(suffix.lower()):
                continue
            scene_id = filename[: -len(suffix)]
            if suffix.lower().startswith("_l1_mss"):
                scene_id += "_L1_MSS"
            return scene_id, mask_kind
    for suffix in GENERIC_CLOUD_SUFFIXES:
        if lowered.endswith(suffix.lower()):
            return filename[: -len(suffix)] + "_L1_MSS", "generic_cloud"
    return None


def classify_generic_cloud(path: Path) -> str:
    parts = [part.lower() for part in path.parts]
    joined = "/".join(parts)
    if (
        "/debug/cloud/dst/" in f"/{joined}/"
        or {"debug", "cloud", "dst"}.issubset(set(parts))
    ):
        return "rdc"
    return "combined"


def _satellite_from_scene(scene_id: str) -> str:
    return scene_id.split("_", 1)[0] if "_" in scene_id else scene_id


def _date_from_scene(scene_id: str, path: Path) -> str:
    for token in scene_id.split("_"):
        if len(token) >= 8 and token[:8].isdigit():
            try:
                return datetime.strptime(token[:8], "%Y%m%d").date().isoformat()
            except ValueError:
                pass
    try:
        modified = os.path.getmtime(filesystem_path(path))
    except OSError:
        modified = time.time()
    return datetime.fromtimestamp(modified).date().isoformat()


def scene_plan_code(scene_id: str) -> str:
    tokens = scene_id.split("_")
    if len(tokens) >= 5 and tokens[3] and tokens[4]:
        return f"{tokens[3]}_{tokens[4]}"
    return ""


def analyze_cloud_haze(
    image_path: str, reference_masks: dict[str, str] | None = None
) -> tuple[float, str]:
    try:
        with open_image_file(image_path) as source:
            image = source.convert("RGB")
            image.thumbnail((256, 256), Image.Resampling.BILINEAR)
            gray = image.convert("L")
            hsv = image.convert("HSV")
            saturation = hsv.getchannel("S")
            value = hsv.getchannel("V")
            gray_stat = ImageStat.Stat(gray)
            sat_stat = ImageStat.Stat(saturation)
            edge_stat = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
            brightness_mean = gray_stat.mean[0] / 255.0
            brightness_std = gray_stat.stddev[0] / 255.0
            saturation_mean = sat_stat.mean[0] / 255.0
            edge_mean = edge_stat.mean[0] / 255.0
            predicted = Image.new("L", image.size, 0)
            predicted_pixels = predicted.load()
            width, _height = image.size
            bright_low_sat = 0
            haze_like = 0
            total = 0
            for bright, sat in zip(value.getdata(), saturation.getdata()):
                position = total
                total += 1
                bright_ratio = bright / 255.0
                sat_ratio = sat / 255.0
                if bright_ratio >= 0.72 and sat_ratio <= 0.22:
                    bright_low_sat += 1
                if bright_ratio >= 0.58 and sat_ratio <= 0.16:
                    haze_like += 1
                if (
                    bright_ratio >= 0.72 and sat_ratio <= 0.24
                ) or (
                    bright_ratio >= 0.58 and sat_ratio <= 0.14
                ):
                    predicted_pixels[position % width, position // width] = 255
            if total <= 0:
                return 0.0, ""
            predicted_ratio = _white_ratio(predicted)
            white_ratio = bright_low_sat / total
            haze_ratio = haze_like / total
            visual_score = min(
                1.0,
                max(
                    0.0,
                    0.42 * white_ratio
                    + 0.24 * haze_ratio
                    + 0.14 * max(0.0, brightness_mean - 0.45) / 0.55
                    + 0.10 * max(0.0, 0.35 - saturation_mean) / 0.35
                    + 0.10 * max(0.0, 0.18 - edge_mean) / 0.18
                    + 0.08 * max(0.0, 0.22 - brightness_std) / 0.22,
                ),
            )
            reference_diffs: list[tuple[str, float, float, float]] = []
            for method, mask_path in (reference_masks or {}).items():
                if method not in {"combined", "rdc"} or not mask_path:
                    continue
                try:
                    with open_image_file(mask_path) as mask_image:
                        ref_ratio, _pred_ratio, diff, iou = compare_mask_images(
                            mask_image, predicted
                        )
                    reference_diffs.append((method, ref_ratio, diff, iou))
                except Exception:
                    continue
            if reference_diffs:
                max_diff = max(item[2] for item in reference_diffs)
                score = min(1.0, 0.45 * visual_score + 0.55 * max_diff)
            else:
                score = visual_score
            reasons: list[str] = []
            if white_ratio >= 0.28:
                reasons.append("高亮低饱和区域较多")
            if haze_ratio >= 0.45:
                reasons.append("疑似薄云或雾")
            if edge_mean <= 0.12:
                reasons.append("纹理对比偏弱")
            if saturation_mean <= 0.20:
                reasons.append("整体饱和度偏低")
            if reference_diffs:
                method_names = {"combined": "综合法", "rdc": "RDC"}
                detail = "；".join(
                    (
                        f"与{method_names.get(method, method)}差异{diff:.1%}"
                        f"/IoU{iou:.2f}"
                    )
                    for method, _ref_ratio, diff, iou in reference_diffs
                )
                reasons.append(f"离线云判云量{predicted_ratio:.1%}，{detail}")
            return score, "，".join(reasons[:3])
    except Exception:
        return 0.0, ""


def season_from_month(month: int) -> str:
    if month in (3, 4, 5):
        return "spring"
    if month in (6, 7, 8):
        return "summer"
    if month in (9, 10, 11):
        return "autumn"
    if month in (12, 1, 2):
        return "winter"
    return "unknown"


def geo_cell_from_center(
    longitude: float | None,
    latitude: float | None,
    grid_degrees: float,
) -> str:
    if longitude is None or latitude is None:
        return "unknown"
    size = max(0.1, float(grid_degrees))
    lon_index = int((longitude + 180.0) // size)
    lat_index = int((latitude + 90.0) // size)
    return f"{size:g}deg:{lon_index}:{lat_index}"


def _xml_local_name(tag: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tag.rsplit("}", 1)[-1].lower())


def _float_value(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", value)
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _xml_values(root: ET.Element) -> dict[str, float]:
    values: dict[str, float] = {}
    for node in root.iter():
        node_name = _xml_local_name(node.tag)
        value = _float_value(node.text)
        if value is not None:
            values.setdefault(node_name, value)
        for attribute, attribute_value in node.attrib.items():
            parsed = _float_value(attribute_value)
            if parsed is None:
                continue
            attribute_name = _xml_local_name(attribute)
            values.setdefault(attribute_name, parsed)
            values.setdefault(f"{node_name}{attribute_name}", parsed)
    return values


def _first_xml_value(values: dict[str, float], aliases: Iterable[str]) -> float | None:
    for alias in aliases:
        value = values.get(_xml_local_name(alias))
        if value is not None:
            return value
    return None


def _nested_xml_corner(
    root: ET.Element,
    container_aliases: Iterable[str],
) -> tuple[float | None, float | None]:
    names = {_xml_local_name(alias) for alias in container_aliases}
    for node in root.iter():
        node_name = _xml_local_name(node.tag)
        descendant_names = {
            _xml_local_name(child.text or "")
            for child in node.iter()
            if child.text and _float_value(child.text) is None
        }
        descendant_names.update(
            _xml_local_name(value)
            for value in node.attrib.values()
            if _float_value(value) is None
        )
        if node_name not in names and not names.intersection(descendant_names):
            continue
        child_values = _xml_values(node)
        longitude = _first_xml_value(
            child_values, ("Longitude", "Longtitude", "Lon", "Long", "X")
        )
        latitude = _first_xml_value(child_values, ("Latitude", "Lat", "Y"))
        if longitude is not None and latitude is not None:
            return longitude, latitude
    return None, None


def read_xml_geography(path: Path) -> tuple[dict[str, list[float]], float | None, float | None]:
    root = ET.parse(path).getroot()
    mapping = {
        "upper_left": ("UpperLeft", "TopLeft", "LeftTop", "UpperLeftPoint", "TopLeftPoint", "LeftTopPoint", "UL"),
        "upper_right": ("UpperRight", "TopRight", "RightTop", "UpperRightPoint", "TopRightPoint", "RightTopPoint", "UR"),
        "lower_right": ("LowerRight", "BottomRight", "RightBottom", "LowerRightPoint", "BottomRightPoint", "RightBottomPoint", "LR"),
        "lower_left": ("LowerLeft", "BottomLeft", "LeftBottom", "LowerLeftPoint", "BottomLeftPoint", "LeftBottomPoint", "LL"),
    }
    values = _xml_values(root)
    corners: dict[str, list[float]] = {}
    for key, aliases in mapping.items():
        longitude = _first_xml_value(
            values,
            [f"{alias}{suffix}" for alias in aliases for suffix in ("Longitude", "Longtitude", "Lon", "Long", "X")],
        )
        latitude = _first_xml_value(
            values,
            [f"{alias}{suffix}" for alias in aliases for suffix in ("Latitude", "Lat", "Y")],
        )
        if longitude is None or latitude is None:
            longitude, latitude = _nested_xml_corner(root, aliases)
        if longitude is not None and latitude is not None:
            if longitude > 180.0:
                longitude -= 360.0
            corners[key] = [longitude, latitude]
    center_lon = _first_xml_value(values, ("CenterLongitude", "CenterLon", "CenterX"))
    center_lat = _first_xml_value(values, ("CenterLatitude", "CenterLat", "CenterY"))
    if center_lon is not None and center_lon > 180.0:
        center_lon -= 360.0
    if (center_lon is None or center_lat is None) and corners:
        center_lon = sum(point[0] for point in corners.values()) / len(corners)
        center_lat = sum(point[1] for point in corners.values()) / len(corners)
    return corners, center_lon, center_lat


def read_shp_geography(path: Path) -> tuple[dict[str, list[float]], float | None, float | None]:
    with path.open("rb") as handle:
        header = handle.read(100)
        polygon_points: list[tuple[float, float]] = []
        while True:
            record_header = handle.read(8)
            if len(record_header) < 8:
                break
            _record_number, content_words = struct.unpack(">2i", record_header)
            content = handle.read(max(0, content_words) * 2)
            if len(content) < 44:
                continue
            shape_type = struct.unpack_from("<i", content, 0)[0]
            if shape_type not in (5, 15, 25):
                continue
            num_parts, num_points = struct.unpack_from("<2i", content, 36)
            points_offset = 44 + max(0, num_parts) * 4
            if num_points < 4 or len(content) < points_offset + num_points * 16:
                continue
            points = [
                struct.unpack_from("<2d", content, points_offset + index * 16)
                for index in range(num_points)
            ]
            if len(points) > 1 and points[0] == points[-1]:
                points.pop()
            if len(points) == 4:
                polygon_points = points
                break
    if len(header) < 68:
        return {}, None, None
    xmin, ymin, xmax, ymax = struct.unpack("<4d", header[36:68])
    if not all(map(lambda value: abs(value) < 1e10, (xmin, ymin, xmax, ymax))):
        return {}, None, None
    if polygon_points:
        normalized = [
            (longitude - 360.0 if longitude > 180.0 else longitude, latitude)
            for longitude, latitude in polygon_points
        ]
        north_to_south = sorted(
            normalized, key=lambda point: point[1], reverse=True
        )
        upper_left, upper_right = sorted(
            north_to_south[:2], key=lambda point: point[0]
        )
        lower_left, lower_right = sorted(
            north_to_south[2:], key=lambda point: point[0]
        )
        corners = {
            "upper_left": list(upper_left),
            "upper_right": list(upper_right),
            "lower_right": list(lower_right),
            "lower_left": list(lower_left),
        }
        return (
            corners,
            sum(point[0] for point in normalized) / 4.0,
            sum(point[1] for point in normalized) / 4.0,
        )
    corners = {
        "upper_left": [xmin, ymax],
        "upper_right": [xmax, ymax],
        "lower_right": [xmax, ymin],
        "lower_left": [xmin, ymin],
    }
    return corners, (xmin + xmax) / 2.0, (ymin + ymax) / 2.0


def read_scene_geography(
    meta_path: str | None,
    shp_path: str | None,
) -> tuple[dict[str, list[float]], float | None, float | None, str]:
    if meta_path:
        try:
            corners, longitude, latitude = read_xml_geography(Path(meta_path))
            if len(corners) == 4:
                return corners, longitude, latitude, meta_path
        except (OSError, ET.ParseError):
            pass
    if shp_path:
        try:
            corners, longitude, latitude = read_shp_geography(Path(shp_path))
            if corners:
                return corners, longitude, latitude, shp_path
        except OSError:
            pass
    return {}, None, None, meta_path or shp_path or ""


def scene_from_group(
    group: dict[str, str],
    config: AppConfig,
    require_comparison: bool = True,
    analyze_image: bool = False,
    calculate_ratios: bool = True,
) -> Scene | None:
    source_value = group.get("source_tif") or group.get("image")
    if not source_value:
        return None
    if require_comparison and ("combined" not in group or "rdc" not in group):
        return None
    ratios: dict[str, float] = {}
    difference = 0.0
    difference_percent = 0.0
    iou = 1.0
    masks = {
        key: value
        for key, value in group.items()
        if key in {*MASK_SUFFIXES, "final"} and value
    }
    compare_size = max(1, int(config.mask_compare_size or 512))
    if calculate_ratios and "combined" in masks and "rdc" in masks:
        combined_ratio, rdc_ratio, difference, iou = compare_masks(
            masks["combined"],
            masks["rdc"],
            target_size=(compare_size, compare_size),
        )
        ratios["combined"] = combined_ratio
        ratios["rdc"] = rdc_ratio
        difference_percent = difference_percent_from_ratios(
            combined_ratio, rdc_ratio
        )
    for kind, path in masks.items():
        if calculate_ratios and kind not in ratios:
            ratios[kind] = cloud_ratio(path, size=(compare_size, compare_size))
    omni_path = masks.get("omnicloudmask")
    if calculate_ratios and omni_path:
        for method in ("combined", "rdc"):
            reference = masks.get(method)
            if not reference:
                continue
            _omni_ratio, ref_ratio, diff, ref_iou = compare_masks(
                omni_path,
                reference,
                target_size=(compare_size, compare_size),
            )
            ratios[f"omnicloudmask_vs_{method}_difference"] = diff
            ratios[f"omnicloudmask_vs_{method}_iou"] = ref_iou
            ratios[f"{method}_ratio_for_omni_compare"] = ref_ratio
    image_path = Path(group.get("image") or source_value)
    source_tif_path = Path(source_value)
    acquired_date = _date_from_scene(group["scene_id"], image_path)
    month = int(acquired_date[5:7])
    corners: dict[str, list[float]] = {}
    center_lon: float | None = None
    center_lat: float | None = None
    metadata_path = ""
    geography_diagnostics: list[str] = []
    meta_path = group.get("meta", "")
    expected_meta = group.get("meta_expected", meta_path)
    if meta_path:
        try:
            corners, center_lon, center_lat = read_xml_geography(Path(meta_path))
            if len(corners) == 4:
                metadata_path = meta_path
                geography_diagnostics.append(f"已读取meta.xml四角坐标：{meta_path}")
            else:
                geography_diagnostics.append(
                    f"meta.xml四角不完整：{meta_path}（{len(corners)}/4）"
                )
                corners = {}
        except (OSError, ET.ParseError) as exc:
            geography_diagnostics.append(f"meta.xml解析失败：{meta_path}：{exc}")
            corners = {}
    elif expected_meta:
        geography_diagnostics.append(f"未找到meta.xml：{expected_meta}")
    shp_path = group.get("shp", "")
    if len(corners) != 4 and shp_path:
        try:
            corners, center_lon, center_lat = read_shp_geography(Path(shp_path))
            if len(corners) == 4:
                metadata_path = shp_path
                geography_diagnostics.append(
                    f"已使用SHP真实多边形顶点：{shp_path}"
                )
        except OSError as exc:
            geography_diagnostics.append(f"SHP读取失败：{shp_path}：{exc}")
    anomaly_score = 0.0
    anomaly_reason = ""
    if analyze_image:
        anomaly_score, anomaly_reason = analyze_cloud_haze(str(image_path), masks)
    return Scene(
        scene_id=group["scene_id"],
        satellite=group["satellite"],
        acquired_date=acquired_date,
        image_path=str(image_path),
        source_tif_path=str(source_tif_path),
        masks=masks,
        plan_code=scene_plan_code(group["scene_id"]),
        metadata_path=metadata_path,
        corners=corners,
        center_lon=center_lon,
        center_lat=center_lat,
        month=month,
        season=season_from_month(month),
        geo_cell=geo_cell_from_center(
            center_lon, center_lat, config.geo_grid_degrees
        ),
        cloud_ratios=ratios,
        anomaly_score=anomaly_score,
        anomaly_reason=anomaly_reason,
        difference=difference,
        difference_percent=difference_percent,
        iou=iou,
        preview_image_path=group.get("preview_image", ""),
        geometry_diagnostic="；".join(geography_diagnostics),
    )


def _scene_date_is_recent(scene_id: str, lookback_days: int) -> bool:
    if lookback_days <= 0:
        return True
    cutoff = datetime.now().date() - timedelta(days=lookback_days)
    for token in scene_id.split("_"):
        if len(token) >= 8 and token[:8].isdigit():
            try:
                return datetime.strptime(token[:8], "%Y%m%d").date() >= cutoff
            except ValueError:
                continue
    return True


def _date_from_directory_parts(path: Path) -> date | None:
    parts = path.parts
    for index in range(len(parts) - 2):
        year, month, day = parts[index : index + 3]
        if (
            len(year) == 4
            and year.isdigit()
            and month.isdigit()
            and day.isdigit()
        ):
            try:
                return datetime(int(year), int(month), int(day)).date()
            except ValueError:
                continue
    return None


def _iter_product_roots(root: Path) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        resolved = path.resolve()
        if resolved not in seen and path.is_dir():
            seen.add(resolved)
            roots.append(path)

    add(root)
    if root.name.upper() == "PRODUCT":
        add(root)
    direct_product = root / "PRODUCT"
    if direct_product.is_dir():
        add(direct_product)
    if not root.is_dir():
        return roots
    for child in root.iterdir():
        if not child.is_dir():
            continue
        if child.name.upper() == "PRODUCT":
            add(child)
        nested_product = child / "PRODUCT"
        if nested_product.is_dir():
            add(nested_product)
    return roots


def _sorted_numeric_dirs(parent: Path, width: int) -> list[Path]:
    return sorted(
        [
            path
            for path in parent.iterdir()
            if path.is_dir() and len(path.name) == width and path.name.isdigit()
        ],
        key=lambda path: path.name,
        reverse=True,
    )


def _is_materialized_scene(path: Path) -> bool:
    scene_id = path.name
    candidates = [
        path / f"{scene_id}.tif",
        path / f"{scene_id}.tiff",
        path / f"{scene_id}.jpg",
        path / f"{scene_id}.jpeg",
        path / f"{scene_id}_cloud.TIF",
    ]
    return any(_path_exists(candidate) for candidate in candidates)


def _iter_scene_dirs_for_satellite(
    satellite_dir: Path,
    plan_filter: str = "",
) -> Iterable[Path]:
    plan_filter = plan_filter.strip().lower()

    def matches_plan(path: Path) -> bool:
        return not plan_filter or plan_filter in path.name.lower()

    seen: set[Path] = set()
    if satellite_dir.name.upper().endswith("_L1_MSS") and _is_materialized_scene(
        satellite_dir
    ) and matches_plan(
        satellite_dir
    ):
        resolved = satellite_dir.resolve()
        seen.add(resolved)
        yield satellite_dir
    for outer_dir in sorted(
        [path for path in satellite_dir.iterdir() if path.is_dir()],
        key=lambda path: path.name,
        reverse=True,
    ):
        candidates: list[Path] = []
        if outer_dir.name.upper().endswith("_L1_MSS") and _is_materialized_scene(
            outer_dir
        ) and matches_plan(outer_dir):
            candidates.append(outer_dir)
        candidates.extend(
            sorted(
                [
                    path
                    for path in outer_dir.iterdir()
                    if path.is_dir()
                    and path.name.upper().endswith("_L1_MSS")
                    and _is_materialized_scene(path)
                    and matches_plan(path)
                ],
                key=lambda path: path.name,
                reverse=True,
            )
        )
        for scene_dir in candidates:
            resolved = scene_dir.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            yield scene_dir


def _find_first_existing(paths: Iterable[Path]) -> str | None:
    for path in paths:
        if _path_exists(path):
            return _display_path(path)
    return None


def _join_path_candidates(paths: Iterable[Path], limit: int = 8) -> str:
    return " | ".join(str(path) for path in list(paths)[:limit])


def _find_direct_pattern_file(
    directory: Path,
    scene_id: str,
    suffix_lower: str,
) -> tuple[str | None, list[str]]:
    if not _path_is_dir(directory):
        return None, []
    preferred_lower = f"{scene_id}{suffix_lower}".lower()
    matches = sorted(
        [
            path
            for path in _iter_child_paths(directory)
            if _path_is_file(path)
            and path.name.lower().endswith(suffix_lower.lower())
        ],
        key=lambda path: (
            0 if path.name.lower() == preferred_lower else 1,
            0 if scene_id.lower() in path.name.lower() else 1,
            path.name.lower(),
        ),
    )
    return (_display_path(matches[0]) if matches else None), [
        _display_path(path) for path in matches[:20]
    ]


def _find_cloud_tif_in_directory(directory: Path, preferred_stem: str) -> str | None:
    if not _path_is_dir(directory):
        return None
    preferred_lower = preferred_stem.lower()
    matches = sorted(
        [
            path
            for path in _iter_child_paths(directory)
            if _path_is_file(path)
            and path.suffix.lower() in {".tif", ".tiff"}
            and "cloud" in path.stem.lower()
        ],
        key=lambda path: (
            0 if path.stem.lower() == f"{preferred_lower}_cloud" else 1,
            0 if preferred_lower in path.stem.lower() else 1,
            path.name.lower(),
        ),
    )
    return str(matches[0].resolve()) if matches else None


def _find_combined_in_directory(directory: Path, scene_id: str) -> tuple[str | None, list[str]]:
    if not _path_is_dir(directory):
        return None, []
    scene_lower = scene_id.lower()
    matches = sorted(
        [
            path
            for path in _iter_child_paths(directory)
            if _path_is_file(path) and path.suffix.lower() in {".tif", ".tiff"}
        ],
        key=lambda path: (
            0 if scene_lower in path.name.lower() else 1,
            0 if "cloud" in path.stem.lower() else 1,
            0 if "combined" in path.stem.lower() else 1,
            path.name.lower(),
        ),
    )
    return (_display_path(matches[0]) if matches else None), [
        _display_path(path) for path in matches[:20]
    ]


def _find_combined_cloud(scene_dir: Path, scene_id: str) -> tuple[str | None, str, list[str]]:
    combined_root = scene_dir / "Debug" / "Cloud" / "combined"
    candidates = (
        combined_root / f"{scene_id}_Cloud.tif",
    )
    found = _find_first_existing(candidates)
    return found, _join_path_candidates(candidates), [
        _display_path(path) for path in candidates if _path_is_file(path)
    ]


def _find_final_cloud(scene_dir: Path, scene_id: str) -> tuple[str | None, str]:
    candidate = scene_dir / f"{scene_id}_cloud.TIF"
    return _find_first_existing((candidate,)), str(candidate)


def _find_rdc_cloud(scene_dir: Path, scene_id: str) -> tuple[str | None, list[str], list[str]]:
    roots = (
        scene_dir / "Debug" / "Cloud" / "dst512",
        scene_dir / "Debug" / "Cloud" / "dst",
        scene_dir / "Cloud" / "dst512",
    )
    paths = tuple(root / f"{scene_id}_Cloud.tif" for root in roots)
    return _find_first_existing(paths), [str(root) for root in roots], [
        str(path) for path in paths
    ]


def _find_cloud_result_in_directory(directory: Path, scene_id: str) -> tuple[str | None, list[str]]:
    if not _path_is_dir(directory):
        return None, []
    scene_lower = scene_id.lower()
    matches = sorted(
        [
            path
            for path in _iter_child_paths(directory)
            if _path_is_file(path)
            and path.suffix.lower() in {".tif", ".tiff"}
            and (
                scene_lower in path.name.lower()
                or "cloud" in path.stem.lower()
                or "mask" in path.stem.lower()
            )
        ],
        key=lambda path: (
            0 if scene_lower in path.name.lower() else 1,
            0 if "cloud" in path.stem.lower() else 1,
            path.name.lower(),
        ),
    )
    return (_display_path(matches[0]) if matches else None), [
        _display_path(path) for path in matches[:20]
    ]


def _find_omnicloudmask_cloud(scene_dir: Path, scene_id: str) -> tuple[str | None, list[str], list[str]]:
    roots = (
        scene_dir / "Debug" / "Cloud" / "dst_omni512",
        scene_dir / "Cloud" / "dst_omni512",
    )
    paths = tuple(
        path
        for root in roots
        for path in (
            root / f"{scene_id}_Cloud.tif",
            root / f"{scene_id}_omnicloudmask_cloud_binary.tif",
        )
    ) + (
        scene_dir / f"{scene_id}_omnicloudmask_cloud_binary.tif",
        scene_dir / f"{scene_id}_omnicloudmask_mask.tif",
    )
    return _find_first_existing(paths), [str(root) for root in roots], [
        str(path) for path in paths
    ]


def find_scene_metadata_xml(scene_dir: Path, scene_id: str) -> str | None:
    """Find product metadata through a small set of fixed, local candidates."""
    stems = [scene_id]
    if scene_id.upper().endswith("_MSS"):
        stems.append(scene_id[:-4])
    directories = [scene_dir]
    nested = scene_dir / scene_id
    if nested.is_dir():
        directories.append(nested)
    directories = list(dict.fromkeys(directories))

    candidates: list[Path] = []
    for directory in directories:
        for stem in stems:
            candidates.extend(
                (
                    directory / f"{stem}_meta.xml",
                    directory / f"{stem}_MSS_meta.xml",
                )
            )
    found = _find_first_existing(candidates)
    if found:
        return found

    return None


def _build_group_from_scene_dir(scene_dir: Path) -> dict[str, str]:
    scene_id = scene_dir.name
    group: dict[str, str] = {
        "scene_id": scene_id,
        "satellite": _satellite_from_scene(scene_id),
    }
    source_candidates = (
            scene_dir / f"{scene_id}.tif",
            scene_dir / f"{scene_id}.tiff",
    )
    group["source_tif_candidates"] = _join_path_candidates(source_candidates)
    group["source_tif"] = _find_first_existing(source_candidates) or ""
    preview_path = _find_first_existing(
        (
            scene_dir / f"{scene_id}_thumb.jpg",
            scene_dir / f"{scene_id}_thumb.jpeg",
        )
    )
    image_path = _find_first_existing(
        (
            scene_dir / f"{scene_id}.jpg",
            scene_dir / f"{scene_id}.jpeg",
            Path(preview_path) if preview_path else scene_dir / "__missing_preview__",
        )
    )
    if image_path:
        group["image"] = image_path
    if preview_path:
        group["preview_image"] = preview_path
    elif image_path:
        group["preview_image"] = image_path
    group["meta_expected"] = str(scene_dir / f"{scene_id}_meta.xml")
    meta_path = find_scene_metadata_xml(scene_dir, scene_id)
    if meta_path:
        group["meta"] = meta_path
    shp_path = _find_first_existing(
        (
            scene_dir / f"{scene_id}_L1_MSS.shp",
            scene_dir / f"{scene_id}.shp",
        )
    )
    if shp_path:
        group["shp"] = shp_path
    combined_path, combined_pattern, combined_matches = _find_combined_cloud(
        scene_dir,
        scene_id,
    )
    group["combined_candidates"] = combined_pattern
    group["combined_matches"] = " | ".join(combined_matches[:20])
    if combined_path:
        group["combined"] = combined_path
    final_path, final_candidate = _find_final_cloud(scene_dir, scene_id)
    group["final_candidate"] = final_candidate
    if final_path:
        group["final"] = final_path
    rdc_path, checked_rdc_dirs, rdc_candidates = _find_rdc_cloud(scene_dir, scene_id)
    group["scene_dir"] = str(scene_dir)
    group["rdc_checked_dirs"] = " | ".join(checked_rdc_dirs[:10])
    group["rdc_candidates"] = " | ".join(rdc_candidates[:30])
    if rdc_path:
        group["rdc"] = rdc_path
    omni_path, checked_omni_dirs, omni_candidates = _find_omnicloudmask_cloud(
        scene_dir,
        scene_id,
    )
    group["omnicloudmask_checked_dirs"] = " | ".join(checked_omni_dirs[:10])
    group["omnicloudmask_candidates"] = " | ".join(omni_candidates[:30])
    if omni_path:
        group["omnicloudmask"] = omni_path
    return group


def discover_scan_groups(config: AppConfig) -> list[ScanGroup]:
    filters = tuple(value.upper() for value in config.satellite_filters if value)
    today = datetime.now().date()
    cutoff_date = (
        today - timedelta(days=config.scan_lookback_days)
        if config.scan_lookback_days > 0
        else None
    )
    groups: list[ScanGroup] = []
    seen: set[tuple[str, str, str]] = set()
    for root_value in config.watch_roots:
        root = Path(root_value).expanduser()
        if not root.exists():
            continue
        for product_root in _iter_product_roots(root):
            for year_dir in _sorted_numeric_dirs(product_root, 4) or [product_root]:
                for month_dir in _sorted_numeric_dirs(year_dir, 2) or [year_dir]:
                    for day_dir in _sorted_numeric_dirs(month_dir, 2) or [month_dir]:
                        directory_date = _date_from_directory_parts(day_dir)
                        if not directory_date:
                            continue
                        if directory_date >= today:
                            continue
                        if cutoff_date and directory_date < cutoff_date:
                            continue
                        if not day_dir.is_dir():
                            continue
                        group_date = directory_date.isoformat()
                        for satellite_dir in sorted(
                            [path for path in day_dir.iterdir() if path.is_dir()],
                            key=lambda path: path.name.upper(),
                        ):
                            satellite_name = satellite_dir.name.upper()
                            if filters and not any(
                                satellite_name.startswith(prefix) for prefix in filters
                            ):
                                continue
                            key = (
                                group_date,
                                satellite_dir.name,
                                str(satellite_dir.resolve()),
                            )
                            if key in seen:
                                continue
                            seen.add(key)
                            groups.append(
                                ScanGroup(
                                    group_date=group_date,
                                    satellite=satellite_dir.name,
                                    path=str(satellite_dir.resolve()),
                                )
                            )
    return sorted(groups, key=lambda item: (item.group_date, item.satellite.upper()))


def _normalized_scene_date(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    return digits[:8] if len(digits) >= 8 else ""


def _scene_date_token(scene_id: str) -> str:
    for token in scene_id.split("_"):
        if len(token) >= 8 and token[:8].isdigit():
            return token[:8]
    return ""


def normalize_scene_id_for_lookup(value: str) -> str:
    scene_id = value.strip().strip(",，;；")
    if scene_id.upper().endswith("_L1_MSS"):
        return scene_id
    if scene_id.upper().endswith("_L1"):
        return f"{scene_id}_MSS"
    return scene_id


def parse_scene_id_list(value: str) -> list[str]:
    scene_ids: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\s,，;+；]+", value):
        scene_id = normalize_scene_id_for_lookup(token)
        if not scene_id or scene_id in seen:
            continue
        seen.add(scene_id)
        scene_ids.append(scene_id)
    return scene_ids


def _scene_matches_filters(
    scene_id: str,
    date_filter: str,
    plan_filter: str,
) -> bool:
    if date_filter and _scene_date_token(scene_id) != date_filter:
        return False
    if not plan_filter:
        return True
    lowered = plan_filter.lower()
    return lowered in scene_id.lower() or lowered == scene_plan_code(scene_id).lower()


def _path_matches_plan_filter(path: Path, plan_filter: str) -> bool:
    lowered = plan_filter.strip().lower()
    if not lowered:
        return True
    return lowered in str(path).lower()


def _collect_scene_dirs_from_path(path: Path, plan_filter: str = "") -> list[Path]:
    root = path if path.is_dir() else path.parent
    if not root.exists():
        return []
    root = root.resolve()
    if (
        root.name.upper().endswith("_L1_MSS")
        and _is_materialized_scene(root)
        and _path_matches_plan_filter(root, plan_filter)
    ):
        return [root]
    found: list[Path] = []
    seen: set[Path] = set()
    for current_root, dirs, _files in os.walk(root):
        current = Path(current_root)
        depth = len(current.relative_to(root).parts)
        if depth > 6:
            dirs[:] = []
            continue
        if (
            current.name.upper().endswith("_L1_MSS")
            and _is_materialized_scene(current)
            and _path_matches_plan_filter(current, plan_filter)
        ):
            resolved = current.resolve()
            if resolved not in seen:
                seen.add(resolved)
                found.append(current)
            dirs[:] = []
    return sorted(found, key=lambda item: item.name, reverse=True)


def _scene_date_tokens_for_lookup(scene_id: str) -> tuple[str, ...]:
    token = _scene_date_token(scene_id)
    if not token:
        return ()
    try:
        scene_date = datetime.strptime(token, "%Y%m%d").date()
    except ValueError:
        return (token,)
    return (
        scene_date.strftime("%Y%m%d"),
        (scene_date + timedelta(days=1)).strftime("%Y%m%d"),
    )


def scene_outer_directory_name(scene_id: str) -> str:
    """Return the product container ID by removing the fourth token from the end."""
    parts = scene_id.split("_")
    if len(parts) < 5:
        return scene_id
    del parts[-4]
    return "_".join(parts)


def _fixed_product_root(root: Path) -> Path:
    if root.name.upper() == "PRODUCT":
        return root
    return root / "GSHC2IMPS" / "PRODUCT"


def _candidate_day_dirs(product_root: Path, scene_id: str) -> Iterable[Path]:
    for token in _scene_date_tokens_for_lookup(scene_id):
        yield product_root / token[:4] / token[4:6] / token[6:8]


def _materialized_scene_dir(path: Path, scene_id: str) -> Path | None:
    if path.name != scene_id:
        return None
    if not path.is_dir() or not _is_materialized_scene(path):
        return None
    return path


def _find_scene_dir_by_id(root: Path, scene_id: str) -> Path | None:
    root = root if root.is_dir() else root.parent
    if not root.exists():
        return None
    root = root.resolve()
    satellite = _satellite_from_scene(scene_id)
    seen: set[Path] = set()

    def check(path: Path) -> Path | None:
        resolved = path.resolve(strict=False)
        if resolved in seen:
            return None
        seen.add(resolved)
        return _materialized_scene_dir(path, scene_id)

    for candidate in (
        root,
        root / scene_id,
        root / satellite / scene_id,
    ):
        found = check(candidate)
        if found:
            return found

    product_root = _fixed_product_root(root)
    outer_name = scene_outer_directory_name(scene_id)
    for day_dir in _candidate_day_dirs(product_root, scene_id):
        satellite_dir = day_dir / satellite
        for candidate in (
            satellite_dir / outer_name / scene_id,
            # Direct fixed fallbacks keep older copied/demo layouts usable.
            satellite_dir / scene_id / scene_id,
            satellite_dir / scene_id,
        ):
            found = check(candidate)
            if found:
                return found
    return None


def find_scene_directories_by_ids(
    config: AppConfig,
    scene_id_text: str,
    explicit_path: str = "",
    limit: int = 0,
) -> tuple[list[Path], list[str]]:
    requested_ids = parse_scene_id_list(scene_id_text)
    if limit > 0:
        requested_ids = requested_ids[:limit]
    roots = (
        [Path(explicit_path).expanduser()]
        if explicit_path.strip()
        else [Path(value).expanduser() for value in config.watch_roots]
    )
    scene_dirs: list[Path] = []
    missing: list[str] = []
    for scene_id in requested_ids:
        scene_dir: Path | None = None
        for root in roots:
            scene_dir = _find_scene_dir_by_id(root, scene_id)
            if scene_dir:
                break
        if not scene_dir:
            missing.append(scene_id)
            continue
        scene_dirs.append(scene_dir)
    return scene_dirs, missing


def scenes_from_directories(
    config: AppConfig,
    scene_dirs: Iterable[Path],
    calculate_ratios: bool = True,
) -> list[Scene]:
    scenes: list[Scene] = []
    seen_scene_ids: set[str] = set()
    for scene_dir in scene_dirs:
        group = _build_group_from_scene_dir(scene_dir)
        scene = scene_from_group(
            group,
            config,
            require_comparison=False,
            analyze_image=config.enable_anomaly_detector,
            calculate_ratios=calculate_ratios,
        )
        if scene is None:
            continue
        if scene.scene_id in seen_scene_ids:
            continue
        seen_scene_ids.add(scene.scene_id)
        scenes.append(scene)
    return scenes


def search_scene_candidates_by_ids(
    config: AppConfig,
    scene_id_text: str,
    explicit_path: str = "",
    limit: int = 0,
) -> tuple[list[Scene], list[str]]:
    scene_dirs, missing = find_scene_directories_by_ids(
        config, scene_id_text, explicit_path, limit
    )
    scenes = scenes_from_directories(config, scene_dirs)
    loaded_ids = {scene.scene_id for scene in scenes}
    for scene_dir in scene_dirs:
        if scene_dir.name not in loaded_ids:
            missing.append(scene_dir.name)
    return scenes, missing


def find_scene_candidate_directories(
    config: AppConfig,
    acquired_date: str = "",
    plan_filter: str = "",
    explicit_path: str = "",
    limit: int = 120,
) -> list[Path]:
    normalized_date = _normalized_scene_date(acquired_date)
    plan_filter = plan_filter.strip()
    explicit_path = explicit_path.strip()

    direct_root = Path(explicit_path).expanduser() if explicit_path else None
    if direct_root and direct_root.is_dir() and direct_root.name.upper().endswith("_L1_MSS"):
        if _is_materialized_scene(direct_root):
            return [direct_root.resolve()]

    if not normalized_date or not plan_filter:
        raise ValueError("组合检索必须同时填写日期和计划号")

    query_date = datetime.strptime(normalized_date, "%Y%m%d").date()
    directory_tokens = (
        query_date.strftime("%Y%m%d"),
        (query_date + timedelta(days=1)).strftime("%Y%m%d"),
    )

    def iter_scene_dirs() -> Iterable[Path]:
        filters = tuple(value.upper() for value in config.satellite_filters if value)
        root_values = [explicit_path] if explicit_path else config.watch_roots
        for root_value in root_values:
            root = Path(root_value).expanduser()
            if not root.exists():
                continue
            product_root = _fixed_product_root(root)
            for token in directory_tokens:
                day_dir = product_root / token[:4] / token[4:6] / token[6:8]
                day_dirs = [day_dir] if day_dir.is_dir() else []
                for day_dir in day_dirs:
                    satellite_dirs = sorted(
                        [item for item in day_dir.iterdir() if item.is_dir()],
                        key=lambda item: item.name,
                        reverse=True,
                    )
                    for satellite_dir in satellite_dirs:
                        satellite_name = satellite_dir.name.upper()
                        if filters and not any(
                            satellite_name.startswith(prefix)
                            for prefix in filters
                        ):
                            continue
                        for outer_dir in satellite_dir.iterdir():
                            if not outer_dir.is_dir() or plan_filter.lower() not in outer_dir.name.lower():
                                continue
                            for scene_dir in outer_dir.iterdir():
                                if (
                                    scene_dir.is_dir()
                                    and scene_dir.name.upper().endswith("_L1_MSS")
                                    and _is_materialized_scene(scene_dir)
                                ):
                                    yield scene_dir

    unique: list[Path] = []
    seen_scene_ids: set[str] = set()
    for scene_dir in iter_scene_dirs():
        scene_id = scene_dir.name
        plan_matches = _scene_matches_filters(
            scene_id, normalized_date, plan_filter
        ) or (
            bool(explicit_path)
            and _scene_matches_filters(scene_id, normalized_date, "")
            and _path_matches_plan_filter(scene_dir, plan_filter)
        )
        if (
            not scene_id
            or scene_id in seen_scene_ids
            or not plan_matches
        ):
            continue
        unique.append(scene_dir)
        seen_scene_ids.add(scene_id)
        if limit > 0 and len(unique) >= limit:
            break
    return sorted(
        unique,
        key=lambda item: item.name,
        reverse=True,
    )


def search_scene_candidates(
    config: AppConfig,
    acquired_date: str = "",
    plan_filter: str = "",
    explicit_path: str = "",
    limit: int = 120,
) -> list[Scene]:
    scene_dirs = find_scene_candidate_directories(
        config, acquired_date, plan_filter, explicit_path, limit
    )
    unique = scenes_from_directories(config, scene_dirs)
    return sorted(
        unique,
        key=lambda item: (item.acquired_date, item.scene_id),
        reverse=True,
    )


def discover_scenes(
    config: AppConfig,
    known_scene: Callable[[str], bool] | None = None,
    on_batch: Callable[[list[Scene]], None] | None = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    batch_size: int = 20,
) -> tuple[list[Scene], dict[str, int]]:
    skipped_known: set[str] = set()
    known_checked: dict[str, bool] = {}
    scenes_buffer: list[Scene] = []
    emitted_count = 0
    filters = tuple(value.upper() for value in config.satellite_filters if value)
    date_dirs_scanned = 0
    satellite_dirs_scanned = 0
    scene_dirs_scanned = 0
    limited = False
    stop_scan = False
    current_scene = ""
    current_decision = ""
    current_difference = ""
    cutoff_date = (
        datetime.now().date() - timedelta(days=config.scan_lookback_days)
        if config.scan_lookback_days > 0
        else None
    )

    def progress_payload() -> dict[str, object]:
        return {
            "directories_scanned": scene_dirs_scanned,
            "files_seen": emitted_count,
            "known_skipped": len(skipped_known),
            "candidate_groups": len(scenes_buffer),
            "emitted": emitted_count,
            "limited": int(limited),
            "date_dirs_scanned": date_dirs_scanned,
            "satellite_dirs_scanned": satellite_dirs_scanned,
            "scene_dirs_scanned": scene_dirs_scanned,
            "current_scene": current_scene,
            "current_decision": current_decision,
            "current_difference": current_difference,
        }

    def flush_ready(force: bool = False) -> list[Scene]:
        nonlocal emitted_count
        if not scenes_buffer:
            return []
        if not force and len(scenes_buffer) < max(1, batch_size):
            return []
        ready = list(scenes_buffer)
        scenes_buffer.clear()
        emitted_count += len(ready)
        if on_batch:
            on_batch(ready)
            return []
        return ready

    def note_scene(
        scene_id: str,
        decision: str,
        difference: float | None = None,
        difference_percent: float | None = None,
    ) -> None:
        nonlocal current_scene, current_decision, current_difference
        current_scene = scene_id
        current_decision = decision
        if difference is None:
            current_difference = ""
        elif difference_percent is None:
            current_difference = f"差异差值 {difference:.2%}"
        else:
            current_difference = (
                f"差异差值 {difference:.2%} / 差异百分比 {difference_percent:.2%}"
            )
        if on_progress:
            on_progress(progress_payload())

    discovered_scenes: list[Scene] = []
    for root_value in config.watch_roots:
        if stop_scan:
            break
        root = Path(root_value).expanduser()
        if not root.exists():
            continue
        for product_root in _iter_product_roots(root):
            year_dirs = _sorted_numeric_dirs(product_root, 4)
            if not year_dirs:
                year_dirs = [product_root]
            for year_dir in year_dirs:
                month_dirs = _sorted_numeric_dirs(year_dir, 2)
                if not month_dirs:
                    month_dirs = [year_dir]
                for month_dir in month_dirs:
                    day_dirs = _sorted_numeric_dirs(month_dir, 2)
                    if not day_dirs:
                        day_dirs = [month_dir]
                    for day_dir in day_dirs:
                        if stop_scan:
                            break
                        directory_date = _date_from_directory_parts(day_dir)
                        if cutoff_date and directory_date and directory_date < cutoff_date:
                            continue
                        if not day_dir.is_dir():
                            continue
                        date_dirs_scanned += 1
                        satellite_dirs = sorted(
                            [path for path in day_dir.iterdir() if path.is_dir()],
                            key=lambda path: path.name,
                            reverse=True,
                        )
                        for satellite_dir in satellite_dirs:
                            satellite_name = satellite_dir.name.upper()
                            if filters and not any(
                                satellite_name.startswith(prefix) for prefix in filters
                            ):
                                continue
                            satellite_dirs_scanned += 1
                            for scene_dir in _iter_scene_dirs_for_satellite(satellite_dir):
                                if scene_dirs_scanned >= config.scan_max_directories:
                                    limited = True
                                    stop_scan = True
                                    break
                                scene_id = scene_dir.name
                                if not _scene_date_is_recent(
                                    scene_id, config.scan_lookback_days
                                ):
                                    continue
                                scene_dirs_scanned += 1
                                note_scene(scene_id, "正在检查")
                                if scene_id in skipped_known:
                                    note_scene(scene_id, "已在已索引列表中")
                                    continue
                                is_known = known_checked.get(scene_id)
                                if is_known is None and known_scene:
                                    is_known = known_scene(scene_id)
                                    known_checked[scene_id] = is_known
                                if is_known:
                                    skipped_known.add(scene_id)
                                    note_scene(scene_id, "跳过已索引")
                                    if on_log and len(skipped_known) <= 20:
                                        on_log(f"跳过已索引 {scene_id}")
                                    continue
                                group = _build_group_from_scene_dir(scene_dir)
                                if not group.get("source_tif"):
                                    note_scene(scene_id, "缺少四波段 L1_MSS.tif")
                                    continue
                                if "combined" not in group:
                                    note_scene(scene_id, "缺少综合法 Cloud.tif")
                                    continue
                                if "rdc" not in group:
                                    checked = group.get("rdc_checked_dirs", "")
                                    detail = f"；已检查 {checked}" if checked else ""
                                    note_scene(scene_id, f"缺少 RDC Cloud.tif{detail}")
                                    continue
                                scene = scene_from_group(group, config)
                                if scene is None:
                                    note_scene(scene_id, "掩膜不完整，跳过")
                                    continue
                                if scene_matches_difference_rules(scene, config):
                                    scene.status = "pending"
                                    decision = "差异命中阈值，加入复核队列"
                                elif (
                                    config.enable_anomaly_detector
                                    and scene.anomaly_score
                                    >= config.anomaly_score_threshold
                                ):
                                    scene.status = "pending"
                                    reason = scene.anomaly_reason or "离线云雾判别命中"
                                    decision = f"差异较小，但疑似云雾异常，加入复核队列 | {reason}"
                                else:
                                    scene.status = "indexed"
                                    decision = "差异较小，不加入复核队列"
                                scenes_buffer.append(scene)
                                note_scene(
                                    scene.scene_id,
                                    decision,
                                    scene.difference,
                                    scene.difference_percent,
                                )
                                if on_log:
                                    on_log(
                                        f"检查 {scene.scene_id} | "
                                        f"综合云量 {scene.cloud_ratios.get('combined', 0):.2%} | "
                                        f"RDC云量 {scene.cloud_ratios.get('rdc', 0):.2%} | "
                                        f"差异差值 {scene.difference:.2%} | "
                                        f"差异百分比 {scene.difference_percent:.2%} | "
                                        f"IoU {scene.iou:.3f} | "
                                        f"离线分 {scene.anomaly_score:.2f} | "
                                        f"{decision}"
                                    )
                                discovered_scenes.extend(flush_ready(False))
                                if emitted_count + len(scenes_buffer) >= config.scan_max_new_scenes:
                                    limited = True
                                    stop_scan = True
                                    break
                            if stop_scan:
                                break
                        if stop_scan:
                            break
                    if stop_scan:
                        break
                if stop_scan:
                    break
            if stop_scan:
                break

    discovered_scenes.extend(flush_ready(True))
    if on_progress:
        on_progress(progress_payload())
    return discovered_scenes, {
        "directories_scanned": scene_dirs_scanned,
        "files_seen": emitted_count,
        "known_skipped": len(skipped_known),
        "candidate_groups": len(scenes_buffer),
        "emitted": emitted_count,
        "limited": int(limited),
        "date_dirs_scanned": date_dirs_scanned,
        "satellite_dirs_scanned": satellite_dirs_scanned,
        "scene_dirs_scanned": scene_dirs_scanned,
    }


def discover_scenes_parallel(
    config: AppConfig,
    known_scene: Callable[[str], bool] | None = None,
    on_seen: Callable[[str], None] | None = None,
    on_batch: Callable[[list[Scene]], None] | None = None,
    on_progress: Callable[[dict[str, object]], None] | None = None,
    on_log: Callable[[str], None] | None = None,
    batch_size: int = 20,
    scan_group: ScanGroup | None = None,
) -> tuple[list[Scene], dict[str, int | float]]:
    skipped_known: set[str] = set()
    known_checked: dict[str, bool] = {}
    scenes_buffer: list[Scene] = []
    discovered_scenes: list[Scene] = []
    emitted_count = 0
    processed_count = 0
    processing_seconds = 0.0
    filters = tuple(value.upper() for value in config.satellite_filters if value)
    date_dirs_scanned = 0
    satellite_dirs_scanned = 0
    scene_dirs_scanned = 0
    limited = False
    stop_scan = False
    current_scene = ""
    current_decision = ""
    current_difference = ""
    scan_started = time.perf_counter()
    max_workers = max(1, min(32, int(config.scan_workers or 1)))
    max_pending = max_workers * 4
    cutoff_date = (
        datetime.now().date() - timedelta(days=config.scan_lookback_days)
        if config.scan_lookback_days > 0
        else None
    )

    def progress_payload() -> dict[str, object]:
        elapsed = max(0.001, time.perf_counter() - scan_started)
        return {
            "directories_scanned": scene_dirs_scanned,
            "files_seen": emitted_count,
            "known_skipped": len(skipped_known),
            "candidate_groups": len(scenes_buffer),
            "emitted": emitted_count,
            "limited": int(limited),
            "date_dirs_scanned": date_dirs_scanned,
            "satellite_dirs_scanned": satellite_dirs_scanned,
            "scene_dirs_scanned": scene_dirs_scanned,
            "current_scene": current_scene,
            "current_decision": current_decision,
            "current_difference": current_difference,
            "elapsed_seconds": round(elapsed, 2),
            "processing_seconds": round(processing_seconds, 2),
            "avg_scene_seconds": (
                round(processing_seconds / processed_count, 3)
                if processed_count
                else 0.0
            ),
            "scene_per_second": round(processed_count / elapsed, 2),
            "scan_workers": max_workers,
        }

    def flush_ready(force: bool = False) -> list[Scene]:
        nonlocal emitted_count
        if not scenes_buffer:
            return []
        if not force and len(scenes_buffer) < max(1, batch_size):
            return []
        ready = list(scenes_buffer)
        scenes_buffer.clear()
        emitted_count += len(ready)
        if on_batch:
            on_batch(ready)
            return []
        return ready

    def note_scene(
        scene_id: str,
        decision: str,
        difference: float | None = None,
        difference_percent: float | None = None,
    ) -> None:
        nonlocal current_scene, current_decision, current_difference
        current_scene = scene_id
        current_decision = decision
        if difference is None:
            current_difference = ""
        elif difference_percent is None:
            current_difference = f"差异差值 {difference:.2%}"
        else:
            current_difference = (
                f"差异差值 {difference:.2%} / 差异百分比 {difference_percent:.2%}"
            )
        if on_progress:
            on_progress(progress_payload())

    def process_scene_dir(scene_dir: Path) -> dict[str, Any]:
        started = time.perf_counter()
        scene_id = scene_dir.name
        try:
            group = _build_group_from_scene_dir(scene_dir)
            path_log = (
                f"路径诊断 {scene_id} | scene_dir={group.get('scene_dir', str(scene_dir))} | "
                f"source_tif={group.get('source_tif', '') or '未命中'} | "
                f"source候选={group.get('source_tif_candidates', '')} | "
                f"综合={group.get('combined', '') or '未命中'} | "
                f"综合规则={group.get('combined_candidates', '')} | "
                f"综合匹配={group.get('combined_matches', '') or '无'} | "
                f"RDC={group.get('rdc', '') or '未命中'} | "
                f"RDC目录={group.get('rdc_checked_dirs', '')} | "
                f"RDC规则和匹配={group.get('rdc_candidates', '') or '无'}"
            )
            if not group.get("source_tif"):
                return {"scene_id": scene_id, "decision": "缺少四波段 L1_MSS.tif", "elapsed": time.perf_counter() - started, "log": path_log}
            if "combined" not in group:
                return {"scene_id": scene_id, "decision": "缺少综合法 Cloud.tif", "elapsed": time.perf_counter() - started, "log": path_log}
            if "rdc" not in group:
                checked = group.get("rdc_checked_dirs", "")
                detail = f"；已检查 {checked}" if checked else ""
                return {"scene_id": scene_id, "decision": f"缺少 RDC Cloud.tif{detail}", "elapsed": time.perf_counter() - started, "log": path_log}
            scene = scene_from_group(group, config)
            if scene is None:
                return {
                    "scene_id": scene_id,
                    "decision": "掩膜不完整，跳过",
                    "elapsed": time.perf_counter() - started,
                    "session_seen": True,
                }
            if scene_matches_difference_rules(scene, config):
                scene.status = "pending"
                decision = "差异命中阈值，加入复核队列"
            elif (
                config.enable_anomaly_detector
                and scene.anomaly_score >= config.anomaly_score_threshold
            ):
                scene.status = "pending"
                reason = scene.anomaly_reason or "离线云雾判别命中"
                decision = f"差异较小，但疑似云雾异常，加入复核队列 | {reason}"
            else:
                scene.status = "indexed"
                decision = "差异较小，不加入复核队列"
            if (
                scene.status == "pending"
                and config.auto_run_omnicloudmask_for_review
                and "omnicloudmask" not in scene.masks
            ):
                if auto_omnicloudmask_allowed(config):
                    try:
                        omni_path = run_omnicloudmask_for_scene(scene, config)
                        attach_omnicloudmask_result(scene, omni_path, config)
                        decision += "；OmniCloudMask 已自动完成"
                    except Exception as exc:
                        scene.cloud_ratios["omnicloudmask_error"] = str(exc)[-500:]
                        decision += f"；OmniCloudMask 自动运行失败：{exc}"
                else:
                    scene.cloud_ratios["omnicloudmask_auto_skipped_daytime"] = 1.0
                    decision += "；白天时段跳过自动 OmniCloudMask"
            elapsed = time.perf_counter() - started
            log_line = (
                f"{path_log}\n"
                f"检查 {scene.scene_id} | "
                f"综合云量 {scene.cloud_ratios.get('combined', 0):.2%} | "
                f"RDC云量 {scene.cloud_ratios.get('rdc', 0):.2%} | "
                f"差异差值 {scene.difference:.2%} | "
                f"差异百分比 {scene.difference_percent:.2%} | "
                f"IoU {scene.iou:.3f} | 离线分 {scene.anomaly_score:.2f} | "
                f"耗时 {elapsed:.2f}s | {decision}"
            )
            return {
                "scene_id": scene.scene_id,
                "scene": scene,
                "decision": decision,
                "elapsed": elapsed,
                "log": log_line,
                "session_seen": True,
            }
        except Exception as exc:
            return {
                "scene_id": scene_id,
                "decision": f"处理失败：{exc}",
                "elapsed": time.perf_counter() - started,
                "log": f"处理失败 {scene_id} | {exc}",
            }

    def handle_result(result: dict[str, Any]) -> None:
        nonlocal limited, stop_scan, processing_seconds, processed_count
        processing_seconds += float(result.get("elapsed") or 0.0)
        processed_count += 1
        if on_seen and result.get("session_seen") and result.get("scene_id"):
            on_seen(str(result["scene_id"]))
        scene = result.get("scene")
        if isinstance(scene, Scene):
            scenes_buffer.append(scene)
            note_scene(
                scene.scene_id,
                str(result.get("decision") or ""),
                scene.difference,
                scene.difference_percent,
            )
            if on_log and result.get("log"):
                on_log(str(result["log"]))
            discovered_scenes.extend(flush_ready(False))
        else:
            note_scene(str(result.get("scene_id") or ""), str(result.get("decision") or ""))
            if on_log and result.get("log"):
                on_log(str(result["log"]))

    def drain_futures(
        pending: set[Future[dict[str, Any]]],
        force_all: bool = False,
    ) -> set[Future[dict[str, Any]]]:
        if not pending:
            return pending
        if force_all:
            done, remaining = wait(pending)
        else:
            done, remaining = wait(pending, return_when=FIRST_COMPLETED)
        for future in done:
            handle_result(future.result())
        return set(remaining)

    pending_futures: set[Future[dict[str, Any]]] = set()
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        if scan_group is not None:
            group_path = Path(scan_group.path)
            date_dirs_scanned = 1
            satellite_dirs_scanned = 1
            for scene_dir in _iter_scene_dirs_for_satellite(group_path):
                if scene_dirs_scanned >= config.scan_max_directories:
                    limited = True
                    stop_scan = True
                    break
                scene_id = scene_dir.name
                scene_dirs_scanned += 1
                note_scene(scene_id, "排队检查")
                is_known = known_checked.get(scene_id)
                if is_known is None and known_scene:
                    is_known = known_scene(scene_id)
                    known_checked[scene_id] = is_known
                if is_known:
                    skipped_known.add(scene_id)
                    note_scene(scene_id, "跳过已处理/本次监控已检查")
                    if on_log and len(skipped_known) <= 20:
                        on_log(f"跳过已处理/本次监控已检查 {scene_id}")
                    continue
                pending_futures.add(executor.submit(process_scene_dir, scene_dir))
                if len(pending_futures) >= max_pending:
                    pending_futures = drain_futures(pending_futures)
        for root_value in ([] if scan_group is not None else config.watch_roots):
            if stop_scan:
                break
            root = Path(root_value).expanduser()
            if not root.exists():
                continue
            for product_root in _iter_product_roots(root):
                for year_dir in _sorted_numeric_dirs(product_root, 4) or [product_root]:
                    for month_dir in _sorted_numeric_dirs(year_dir, 2) or [year_dir]:
                        for day_dir in _sorted_numeric_dirs(month_dir, 2) or [month_dir]:
                            if stop_scan:
                                break
                            directory_date = _date_from_directory_parts(day_dir)
                            if cutoff_date and directory_date and directory_date < cutoff_date:
                                continue
                            if not day_dir.is_dir():
                                continue
                            date_dirs_scanned += 1
                            satellite_dirs = sorted(
                                [path for path in day_dir.iterdir() if path.is_dir()],
                                key=lambda path: path.name,
                                reverse=True,
                            )
                            for satellite_dir in satellite_dirs:
                                satellite_name = satellite_dir.name.upper()
                                if filters and not any(
                                    satellite_name.startswith(prefix)
                                    for prefix in filters
                                ):
                                    continue
                                satellite_dirs_scanned += 1
                                for scene_dir in _iter_scene_dirs_for_satellite(satellite_dir):
                                    if scene_dirs_scanned >= config.scan_max_directories:
                                        limited = True
                                        stop_scan = True
                                        break
                                    scene_id = scene_dir.name
                                    if not _scene_date_is_recent(
                                        scene_id, config.scan_lookback_days
                                    ):
                                        continue
                                    scene_dirs_scanned += 1
                                    note_scene(scene_id, "排队检查")
                                    is_known = known_checked.get(scene_id)
                                    if is_known is None and known_scene:
                                        is_known = known_scene(scene_id)
                                        known_checked[scene_id] = is_known
                                    if is_known:
                                        skipped_known.add(scene_id)
                                        note_scene(scene_id, "跳过已处理/本次监控已检查")
                                        if on_log and len(skipped_known) <= 20:
                                            on_log(f"跳过已处理/本次监控已检查 {scene_id}")
                                        continue
                                    pending_futures.add(
                                        executor.submit(process_scene_dir, scene_dir)
                                    )
                                    if len(pending_futures) >= max_pending:
                                        pending_futures = drain_futures(pending_futures)
                                    if stop_scan:
                                        break
                                if stop_scan:
                                    break
                            if stop_scan:
                                break
                        if stop_scan:
                            break
                    if stop_scan:
                        break
                if stop_scan:
                    break
        while pending_futures:
            pending_futures = drain_futures(pending_futures, force_all=stop_scan)

    discovered_scenes.extend(flush_ready(True))
    if on_progress:
        on_progress(progress_payload())
    return discovered_scenes, {
        "directories_scanned": scene_dirs_scanned,
        "files_seen": emitted_count,
        "known_skipped": len(skipped_known),
        "candidate_groups": len(scenes_buffer),
        "emitted": emitted_count,
        "limited": int(limited),
        "date_dirs_scanned": date_dirs_scanned,
        "satellite_dirs_scanned": satellite_dirs_scanned,
        "scene_dirs_scanned": scene_dirs_scanned,
        "elapsed_seconds": round(time.perf_counter() - scan_started, 2),
        "processing_seconds": round(processing_seconds, 2),
        "avg_scene_seconds": (
            round(processing_seconds / processed_count, 3)
            if processed_count
            else 0.0
        ),
        "scene_per_second": round(
            processed_count / max(0.001, time.perf_counter() - scan_started), 2
        ),
        "scan_workers": max_workers,
    }


class ReviewStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._create_schema()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _create_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS scenes (
                    scene_id TEXT PRIMARY KEY,
                    satellite TEXT NOT NULL,
                    acquired_date TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    source_tif_path TEXT NOT NULL DEFAULT '',
                    plan_code TEXT NOT NULL DEFAULT '',
                    metadata_path TEXT NOT NULL DEFAULT '',
                    corners_json TEXT NOT NULL DEFAULT '{}',
                    center_lon REAL,
                    center_lat REAL,
                    month INTEGER NOT NULL DEFAULT 0,
                    season TEXT NOT NULL DEFAULT 'unknown',
                    geo_cell TEXT NOT NULL DEFAULT 'unknown',
                    masks_json TEXT NOT NULL,
                    ratios_json TEXT NOT NULL,
                    anomaly_score REAL NOT NULL DEFAULT 0,
                    anomaly_reason TEXT NOT NULL DEFAULT '',
                    difference REAL NOT NULL,
                    difference_percent REAL NOT NULL DEFAULT 0,
                    iou REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    review_batch TEXT NOT NULL DEFAULT '',
                    review_count INTEGER NOT NULL DEFAULT 0,
                    last_seen TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scene_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    selected_method TEXT,
                    selected_mask_path TEXT,
                    label_type TEXT NOT NULL DEFAULT 'cloud',
                    dataset_split TEXT,
                    note TEXT,
                    reviewed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dataset_items (
                    scene_id TEXT PRIMARY KEY,
                    satellite TEXT NOT NULL,
                    dataset_split TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    mask_path TEXT NOT NULL,
                    source_method TEXT NOT NULL,
                    label_type TEXT NOT NULL DEFAULT 'cloud',
                    center_lon REAL,
                    center_lat REAL,
                    month INTEGER NOT NULL DEFAULT 0,
                    season TEXT NOT NULL DEFAULT 'unknown',
                    geo_cell TEXT NOT NULL DEFAULT 'unknown',
                    corners_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pending_imports (
                    scene_id TEXT PRIMARY KEY,
                    satellite TEXT NOT NULL,
                    acquired_date TEXT NOT NULL,
                    image_path TEXT NOT NULL,
                    mask_path TEXT NOT NULL,
                    source_method TEXT NOT NULL,
                    label_type TEXT NOT NULL DEFAULT 'cloud',
                    note TEXT NOT NULL DEFAULT '',
                    center_lon REAL,
                    center_lat REAL,
                    month INTEGER NOT NULL DEFAULT 0,
                    season TEXT NOT NULL DEFAULT 'unknown',
                    geo_cell TEXT NOT NULL DEFAULT 'unknown',
                    corners_json TEXT NOT NULL DEFAULT '{}',
                    staged_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_scene_status ON scenes(status);
                CREATE INDEX IF NOT EXISTS idx_scene_date ON scenes(acquired_date);
                CREATE INDEX IF NOT EXISTS idx_pending_staged_at
                    ON pending_imports(staged_at);
                CREATE TABLE IF NOT EXISTS monitor_seen (
                    session_id TEXT NOT NULL,
                    scene_id TEXT NOT NULL,
                    seen_at TEXT NOT NULL,
                    PRIMARY KEY (session_id, scene_id)
                );
                CREATE INDEX IF NOT EXISTS idx_monitor_seen_session
                    ON monitor_seen(session_id);
                CREATE TABLE IF NOT EXISTS scan_groups (
                    group_date TEXT NOT NULL,
                    satellite TEXT NOT NULL,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    total_scenes INTEGER NOT NULL DEFAULT 0,
                    checked_scenes INTEGER NOT NULL DEFAULT 0,
                    review_scenes INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY (group_date, satellite, path)
                );
                CREATE INDEX IF NOT EXISTS idx_scan_groups_status
                    ON scan_groups(status);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(scenes)").fetchall()
            }
            scene_migrations = {
                "source_tif_path": "TEXT NOT NULL DEFAULT ''",
                "plan_code": "TEXT NOT NULL DEFAULT ''",
                "metadata_path": "TEXT NOT NULL DEFAULT ''",
                "corners_json": "TEXT NOT NULL DEFAULT '{}'",
                "center_lon": "REAL",
                "center_lat": "REAL",
                "month": "INTEGER NOT NULL DEFAULT 0",
                "season": "TEXT NOT NULL DEFAULT 'unknown'",
                "geo_cell": "TEXT NOT NULL DEFAULT 'unknown'",
                "anomaly_score": "REAL NOT NULL DEFAULT 0",
                "anomaly_reason": "TEXT NOT NULL DEFAULT ''",
                "difference_percent": "REAL NOT NULL DEFAULT 0",
                "review_batch": "TEXT NOT NULL DEFAULT ''",
            }
            for name, declaration in scene_migrations.items():
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE scenes ADD COLUMN {name} {declaration}"
                    )
            dataset_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(dataset_items)"
                ).fetchall()
            }
            dataset_migrations = {
                "label_type": "TEXT NOT NULL DEFAULT 'cloud'",
                "center_lon": "REAL",
                "center_lat": "REAL",
                "month": "INTEGER NOT NULL DEFAULT 0",
                "season": "TEXT NOT NULL DEFAULT 'unknown'",
                "geo_cell": "TEXT NOT NULL DEFAULT 'unknown'",
                "corners_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for name, declaration in dataset_migrations.items():
                if name not in dataset_columns:
                    connection.execute(
                        f"ALTER TABLE dataset_items ADD COLUMN {name} {declaration}"
                    )
            review_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(reviews)").fetchall()
            }
            if "label_type" not in review_columns:
                connection.execute(
                    "ALTER TABLE reviews ADD COLUMN label_type TEXT NOT NULL DEFAULT 'cloud'"
                )
            pending_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(pending_imports)"
                ).fetchall()
            }
            if "label_type" not in pending_columns:
                connection.execute(
                    "ALTER TABLE pending_imports ADD COLUMN label_type TEXT NOT NULL DEFAULT 'cloud'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_scene_geo_cell ON scenes(geo_cell)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_scene_month ON scenes(month)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_dataset_geo_cell ON dataset_items(geo_cell)"
            )

    def monitor_seen_ids(self, session_id: str) -> set[str]:
        if not session_id:
            return set()
        with self.connect() as connection:
            return {
                row["scene_id"]
                for row in connection.execute(
                    "SELECT scene_id FROM monitor_seen WHERE session_id=?",
                    (session_id,),
                ).fetchall()
            }

    def mark_monitor_seen(self, session_id: str, scene_id: str) -> None:
        if not session_id or not scene_id:
            return
        with self.connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO monitor_seen (session_id, scene_id, seen_at)
                VALUES (?, ?, ?)
                """,
                (session_id, scene_id, datetime.now().isoformat(timespec="seconds")),
            )

    def reset_in_progress_scan_groups(self) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            cursor = connection.execute(
                """
                UPDATE scan_groups
                SET status='pending', checked_scenes=0, review_scenes=0,
                    started_at='', completed_at='', updated_at=?
                WHERE status='in_progress'
                """,
                (now,),
            )
            return int(cursor.rowcount or 0)

    def sync_scan_groups(self, groups: Iterable[ScanGroup]) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            for group in groups:
                connection.execute(
                    """
                    INSERT INTO scan_groups (
                        group_date, satellite, path, status, updated_at
                    ) VALUES (?, ?, ?, 'pending', ?)
                    ON CONFLICT(group_date, satellite, path) DO UPDATE SET
                        path=excluded.path,
                        updated_at=CASE
                            WHEN scan_groups.status='completed'
                            THEN scan_groups.updated_at
                            ELSE excluded.updated_at
                        END
                    """,
                    (group.group_date, group.satellite, group.path, now),
                )

    def next_scan_group(self, groups: Iterable[ScanGroup]) -> ScanGroup | None:
        current_groups = list(groups)
        self.sync_scan_groups(current_groups)
        if not current_groups:
            return None
        current_keys = {
            (group.group_date, group.satellite, group.path) for group in current_groups
        }
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scan_groups
                ORDER BY group_date ASC, satellite ASC, path ASC
                """
            ).fetchall()
        for row in rows:
            key = (row["group_date"], row["satellite"], row["path"])
            if key not in current_keys or row["status"] == "completed":
                continue
            return ScanGroup(
                group_date=row["group_date"],
                satellite=row["satellite"],
                path=row["path"],
                status=row["status"],
                total_scenes=int(row["total_scenes"] or 0),
                review_scenes=int(row["review_scenes"] or 0),
                checked_scenes=int(row["checked_scenes"] or 0),
            )
        return None

    def mark_scan_group_started(self, group: ScanGroup) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scan_groups
                SET status='in_progress', checked_scenes=0, review_scenes=0,
                    started_at=?, completed_at='', updated_at=?
                WHERE group_date=? AND satellite=? AND path=?
                """,
                (now, now, group.group_date, group.satellite, group.path),
            )

    def mark_scan_group_completed(
        self, group: ScanGroup, total_scenes: int, review_scenes: int
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scan_groups
                SET status='completed', total_scenes=?, checked_scenes=?,
                    review_scenes=?, completed_at=?, updated_at=?
                WHERE group_date=? AND satellite=? AND path=?
                """,
                (
                    int(total_scenes),
                    int(total_scenes),
                    int(review_scenes),
                    now,
                    now,
                    group.group_date,
                    group.satellite,
                    group.path,
                ),
            )

    def mark_scan_group_failed(self, group: ScanGroup) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scan_groups
                SET status='pending', checked_scenes=0, review_scenes=0,
                    started_at='', completed_at='', updated_at=?
                WHERE group_date=? AND satellite=? AND path=?
                """,
                (now, group.group_date, group.satellite, group.path),
            )

    def reset_scan_group_record(
        self,
        group_date: str,
        satellite: str,
        path: str | None = None,
    ) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        if path:
            sql = """
                UPDATE scan_groups
                SET status='pending', total_scenes=0, checked_scenes=0,
                    review_scenes=0, started_at='', completed_at='', updated_at=?
                WHERE group_date=? AND satellite=? AND path=?
            """
            params: tuple[object, ...] = (now, group_date, satellite, path)
        else:
            sql = """
                UPDATE scan_groups
                SET status='pending', total_scenes=0, checked_scenes=0,
                    review_scenes=0, started_at='', completed_at='', updated_at=?
                WHERE group_date=? AND satellite=?
            """
            params = (now, group_date, satellite)
        with self.connect() as connection:
            cursor = connection.execute(sql, params)
            return int(cursor.rowcount or 0)

    def scan_group_records(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scan_groups
                ORDER BY group_date ASC, satellite ASC, path ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_scenes(self, scenes: Iterable[Scene]) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        count = 0
        with self.connect() as connection:
            for scene in scenes:
                connection.execute(
                    """
                    INSERT INTO scenes (
                        scene_id, satellite, acquired_date, image_path, source_tif_path, plan_code,
                        metadata_path, corners_json, center_lon, center_lat, month,
                        season, geo_cell, masks_json, ratios_json, anomaly_score,
                        anomaly_reason, difference, difference_percent, iou, status,
                        review_batch, last_seen, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scene_id) DO UPDATE SET
                        satellite=excluded.satellite,
                        acquired_date=excluded.acquired_date,
                        image_path=excluded.image_path,
                        source_tif_path=excluded.source_tif_path,
                        plan_code=excluded.plan_code,
                        metadata_path=excluded.metadata_path,
                        corners_json=excluded.corners_json,
                        center_lon=excluded.center_lon,
                        center_lat=excluded.center_lat,
                        month=excluded.month,
                        season=excluded.season,
                        geo_cell=excluded.geo_cell,
                        masks_json=excluded.masks_json,
                        ratios_json=excluded.ratios_json,
                        anomaly_score=excluded.anomaly_score,
                        anomaly_reason=excluded.anomaly_reason,
                        difference=excluded.difference,
                        difference_percent=excluded.difference_percent,
                        iou=excluded.iou,
                        review_batch=CASE
                            WHEN excluded.review_batch<>'' THEN excluded.review_batch
                            ELSE scenes.review_batch
                        END,
                        last_seen=excluded.last_seen,
                        updated_at=excluded.updated_at
                    """,
                    (
                        scene.scene_id,
                        scene.satellite,
                        scene.acquired_date,
                        scene.image_path,
                        scene.source_tif_path,
                        scene.plan_code,
                        scene.metadata_path,
                        json.dumps(scene.corners, ensure_ascii=False),
                        scene.center_lon,
                        scene.center_lat,
                        scene.month,
                        scene.season,
                        scene.geo_cell,
                        json.dumps(scene.masks, ensure_ascii=False),
                        json.dumps(scene.cloud_ratios, ensure_ascii=False),
                        scene.anomaly_score,
                        scene.anomaly_reason,
                        scene.difference,
                        scene.difference_percent,
                        scene.iou,
                        scene.status or "pending",
                        scene.review_batch,
                        now,
                        now,
                    ),
                )
                count += 1
        return count

    def has_scene(self, scene_id: str) -> bool:
        with self.connect() as connection:
            return (
                connection.execute(
                    "SELECT 1 FROM scenes WHERE scene_id=? LIMIT 1", (scene_id,)
                ).fetchone()
                is not None
            )

    def known_scene_ids(self, lookback_days: int) -> set[str]:
        sql = """
            SELECT scene_id FROM dataset_items
            UNION
            SELECT scene_id FROM pending_imports
        """
        with self.connect() as connection:
            return {
                row["scene_id"]
                for row in connection.execute(sql).fetchall()
            }

    def clear_scan_cache(self) -> int:
        with self.connect() as connection:
            before = int(
                connection.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
            )
            connection.execute(
                """
                DELETE FROM scenes
                WHERE status NOT IN ('pending', 'manual')
                  AND scene_id NOT IN (SELECT scene_id FROM dataset_items)
                  AND scene_id NOT IN (SELECT scene_id FROM pending_imports)
                """
            )
            after = int(
                connection.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
            )
        return before - after

    def database_scene_ids(self, lookback_days: int = 0) -> set[str]:
        sql = "SELECT scene_id FROM dataset_items"
        with self.connect() as connection:
            return {
                row["scene_id"]
                for row in connection.execute(sql).fetchall()
            }

    def queue(
        self,
        strategy: str,
        limit: int,
        minimum_difference: float,
        include_below_threshold: bool = False,
        anomaly_score_threshold: float = 0.0,
        enable_anomaly_detector: bool = False,
        minimum_difference_percent: float = 0.0,
        minimum_difference_for_percent: float = 0.001,
        enable_difference_delta: bool = True,
        enable_difference_percent: bool = False,
    ) -> list[Scene]:
        where = "status IN ('pending', 'manual')"
        parameters: list[object] = []
        if not include_below_threshold:
            threshold_clauses = ["status='manual'"]
            if enable_difference_delta:
                threshold_clauses.append("difference >= ?")
                parameters.append(minimum_difference)
            if enable_difference_percent:
                threshold_clauses.append(
                    "(difference >= ? AND difference_percent >= ?)"
                )
                parameters.extend(
                    [minimum_difference_for_percent, minimum_difference_percent]
                )
            if enable_anomaly_detector:
                threshold_clauses.append("anomaly_score >= ?")
                parameters.append(anomaly_score_threshold)
            where += " AND (" + " OR ".join(threshold_clauses) + ")"
        order = "acquired_date ASC, scene_id ASC"
        sql = f"SELECT * FROM scenes WHERE {where} ORDER BY {order}"
        with self.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._row_to_scene(row) for row in rows]

    def review_queue(self, strategy: str = "difference", batch_id: str = "") -> list[Scene]:
        if strategy == "date":
            order = "acquired_date ASC, scene_id ASC"
        elif strategy == "percent":
            order = "difference_percent DESC, difference DESC, acquired_date ASC"
        elif strategy == "ai":
            order = "anomaly_score DESC, difference DESC, acquired_date ASC"
        else:
            order = "difference DESC, difference_percent DESC, acquired_date ASC"
        parameters: list[object] = []
        where = "status IN ('pending', 'manual')"
        if batch_id:
            where += " AND review_batch=?"
            parameters.append(batch_id)
        sql = f"""
            SELECT * FROM scenes
            WHERE {where}
            ORDER BY {order}
        """
        with self.connect() as connection:
            rows = connection.execute(sql, parameters).fetchall()
        return [self._row_to_scene(row) for row in rows]

    def review_queue_for_cache_date(
        self, cache_date: str, strategy: str = "difference"
    ) -> list[Scene]:
        scenes = self.review_queue(strategy, "")
        filtered: list[Scene] = []
        for scene in scenes:
            cache_path = str(scene.cloud_ratios.get("review_cache_path") or "")
            if not cache_path:
                continue
            folder = Path(cache_path)
            if folder.parent.name != cache_date:
                continue
            if (folder / "_reviewed.txt").exists():
                continue
            filtered.append(scene)
        return filtered

    def review_cache_scenes_for_date(self, cache_date: str) -> list[Scene]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scenes
                ORDER BY acquired_date ASC, difference DESC, scene_id ASC
                """
            ).fetchall()
        filtered: list[Scene] = []
        for row in rows:
            scene = self._row_to_scene(row)
            cache_path = str(scene.cloud_ratios.get("review_cache_path") or "")
            if not cache_path:
                continue
            folder = Path(cache_path)
            if folder.parent.name == cache_date:
                filtered.append(scene)
        return filtered

    def review_batch_scenes(self, batch_id: str) -> list[Scene]:
        if not batch_id:
            return []
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scenes
                WHERE review_batch=?
                ORDER BY acquired_date ASC, difference DESC, scene_id ASC
                """,
                (batch_id,),
            ).fetchall()
        return [self._row_to_scene(row) for row in rows]

    def pending_omnicloudmask_queue(self, limit: int = 1) -> list[Scene]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scenes
                WHERE (
                    status IN ('pending', 'manual')
                    OR (
                        status IN ('staged', 'skipped')
                        AND ratios_json LIKE '%review_cache_path%'
                    )
                )
                  AND masks_json NOT LIKE '%omnicloudmask%'
                ORDER BY acquired_date ASC, difference DESC, scene_id ASC
                LIMIT ?
                """,
                (max(1, int(limit)),),
            ).fetchall()
        scenes = [self._row_to_scene(row) for row in rows]
        return [
            scene
            for scene in scenes
            if "omnicloudmask" not in scene.masks
            and not scene_has_cached_omnicloudmask(scene)
            and bool(scene.source_tif_path)
            and path_exists(scene.source_tif_path)
        ]

    def latest_review_batch(self) -> str:
        with self.connect() as connection:
            row = connection.execute(
                """
                SELECT review_batch FROM scenes
                WHERE status IN ('pending', 'manual') AND review_batch<>''
                GROUP BY review_batch
                ORDER BY MAX(updated_at) DESC
                LIMIT 1
                """
            ).fetchone()
        return str(row["review_batch"]) if row else ""

    def review_cache_path_for_scene(self, scene_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT ratios_json FROM scenes WHERE scene_id=?",
                (scene_id,),
            ).fetchone()
        if not row:
            return ""
        try:
            ratios = json.loads(row["ratios_json"] or "{}")
        except json.JSONDecodeError:
            return ""
        return str(ratios.get("review_cache_path") or "")

    def mark_review_cache_done(self, scene_id: str, decision: str) -> None:
        cache_path = self.review_cache_path_for_scene(scene_id)
        if not cache_path:
            return
        try:
            folder = Path(cache_path)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "_reviewed.txt").write_text(
                f"{datetime.now().isoformat(timespec='seconds')} {decision}\n",
                encoding="utf-8",
            )
        except OSError:
            return

    def unmark_review_cache_done(self, scene_id: str) -> None:
        cache_path = self.review_cache_path_for_scene(scene_id)
        if not cache_path:
            return
        try:
            marker = Path(cache_path) / "_reviewed.txt"
            if marker.exists():
                marker.unlink()
        except OSError:
            return

    def restore_for_review(self, scene_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scenes SET status='pending', updated_at=?
                WHERE scene_id=?
                """,
                (datetime.now().isoformat(timespec="seconds"), scene_id),
            )
        self.unmark_review_cache_done(scene_id)

    def mark_cache_missing(self, scene_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scenes SET status='cache_missing', updated_at=?
                WHERE scene_id=? AND status IN ('pending', 'manual')
                """,
                (datetime.now().isoformat(timespec="seconds"), scene_id),
            )

    @staticmethod
    def _row_to_scene(row: sqlite3.Row) -> Scene:
        return Scene(
            scene_id=row["scene_id"],
            satellite=row["satellite"],
            acquired_date=row["acquired_date"],
            image_path=row["image_path"],
            source_tif_path=row["source_tif_path"],
            plan_code=row["plan_code"] if "plan_code" in row.keys() else "",
            metadata_path=row["metadata_path"],
            corners=json.loads(row["corners_json"] or "{}"),
            center_lon=row["center_lon"],
            center_lat=row["center_lat"],
            month=row["month"],
            season=row["season"],
            geo_cell=row["geo_cell"],
            masks=json.loads(row["masks_json"]),
            cloud_ratios=json.loads(row["ratios_json"]),
            anomaly_score=row["anomaly_score"] if "anomaly_score" in row.keys() else 0.0,
            anomaly_reason=row["anomaly_reason"] if "anomaly_reason" in row.keys() else "",
            difference=row["difference"],
            difference_percent=(
                row["difference_percent"] if "difference_percent" in row.keys() else 0.0
            ),
            iou=row["iou"],
            status=row["status"],
            review_batch=row["review_batch"] if "review_batch" in row.keys() else "",
        )

    def mark_skipped(self, scene_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scenes SET status='skipped', review_count=review_count+1,
                    updated_at=? WHERE scene_id=?
                """,
                (datetime.now().isoformat(timespec="seconds"), scene_id),
            )
        self.mark_review_cache_done(scene_id, "skipped")

    def mark_manual(self, scene_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE scenes SET status='manual', updated_at=?
                WHERE scene_id=?
                """,
                (datetime.now().isoformat(timespec="seconds"), scene_id),
            )

    def add_review(
        self,
        scene: Scene,
        decision: str,
        method: str,
        mask_path: str,
        dataset_split: str,
        note: str,
        label_type: str = "cloud",
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO reviews (
                    scene_id, decision, selected_method, selected_mask_path,
                    label_type, dataset_split, note, reviewed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    scene.scene_id,
                    decision,
                    method,
                    mask_path,
                    normalize_label_type(label_type),
                    dataset_split,
                    note,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE scenes SET status=?, review_count=review_count+1,
                    updated_at=? WHERE scene_id=?
                """,
                (decision, now, scene.scene_id),
            )

    def add_dataset_item(
        self,
        scene: Scene,
        dataset_split: str,
        image_path: str,
        mask_path: str,
        method: str,
        label_type: str = "cloud",
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO dataset_items (
                    scene_id, satellite, dataset_split, image_path, mask_path,
                    source_method, label_type, center_lon, center_lat, month, season,
                    geo_cell, corners_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scene_id) DO UPDATE SET
                    dataset_split=excluded.dataset_split,
                    image_path=excluded.image_path,
                    mask_path=excluded.mask_path,
                    source_method=excluded.source_method
                    ,label_type=excluded.label_type
                    ,center_lon=excluded.center_lon
                    ,center_lat=excluded.center_lat
                    ,month=excluded.month
                    ,season=excluded.season
                    ,geo_cell=excluded.geo_cell
                    ,corners_json=excluded.corners_json
                """,
                (
                    scene.scene_id,
                    scene.satellite,
                    dataset_split,
                    image_path,
                    mask_path,
                    method,
                    normalize_label_type(label_type),
                    scene.center_lon,
                    scene.center_lat,
                    scene.month,
                    scene.season,
                    scene.geo_cell,
                    json.dumps(scene.corners, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )

    def stage_import(
        self,
        scene: Scene,
        image_path: str,
        mask_path: str,
        method: str,
        note: str,
        label_type: str = "cloud",
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO pending_imports (
                    scene_id, satellite, acquired_date, image_path, mask_path,
                    source_method, label_type, note, center_lon, center_lat, month, season,
                    geo_cell, corners_json, staged_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scene_id) DO UPDATE SET
                    image_path=excluded.image_path,
                    mask_path=excluded.mask_path,
                    source_method=excluded.source_method,
                    label_type=excluded.label_type,
                    note=excluded.note,
                    center_lon=excluded.center_lon,
                    center_lat=excluded.center_lat,
                    month=excluded.month,
                    season=excluded.season,
                    geo_cell=excluded.geo_cell,
                    corners_json=excluded.corners_json,
                    staged_at=excluded.staged_at
                """,
                (
                    scene.scene_id,
                    scene.satellite,
                    scene.acquired_date,
                    image_path,
                    mask_path,
                    method,
                    normalize_label_type(label_type),
                    note,
                    scene.center_lon,
                    scene.center_lat,
                    scene.month,
                    scene.season,
                    scene.geo_cell,
                    json.dumps(scene.corners, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE scenes SET status='staged', review_count=review_count+1,
                    updated_at=? WHERE scene_id=?
                """,
                (now, scene.scene_id),
            )
        self.mark_review_cache_done(scene.scene_id, f"staged:{method}")

    def pending_imports(self) -> list[dict[str, object]]:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT scene_id, satellite, acquired_date, image_path, mask_path,
                       source_method, label_type, note, center_lon, center_lat, month, season,
                       geo_cell, corners_json, staged_at
                FROM pending_imports ORDER BY staged_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def pending_count(self) -> int:
        with self.connect() as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM pending_imports"
                ).fetchone()[0]
            )

    def review_batch_count(self, batch_id: str) -> int:
        if not batch_id:
            return 0
        with self.connect() as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM scenes
                    WHERE status IN ('pending', 'manual') AND review_batch=?
                    """,
                    (batch_id,),
                ).fetchone()[0]
            )

    def remove_pending_import(self, scene_id: str) -> dict[str, object] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM pending_imports WHERE scene_id=?", (scene_id,)
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "DELETE FROM pending_imports WHERE scene_id=?", (scene_id,)
            )
            connection.execute(
                """
                UPDATE scenes SET status='pending', updated_at=?
                WHERE scene_id=?
                """,
                (datetime.now().isoformat(timespec="seconds"), scene_id),
            )
        return dict(row)

    def complete_pending_import(
        self,
        scene: Scene,
        method: str,
        final_mask_path: str,
        dataset_split: str,
        note: str,
        label_type: str = "cloud",
    ) -> None:
        self.add_review(
            scene,
            "accepted",
            method,
            final_mask_path,
            dataset_split,
            note,
            label_type,
        )
        with self.connect() as connection:
            connection.execute(
                "DELETE FROM pending_imports WHERE scene_id=?", (scene.scene_id,)
            )

    def get_scene(self, scene_id: str) -> Scene | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM scenes WHERE scene_id=?", (scene_id,)
            ).fetchone()
        return self._row_to_scene(row) if row is not None else None

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM scenes GROUP BY status"
            ).fetchall()
            dataset_count = connection.execute(
                "SELECT COUNT(*) FROM dataset_items"
            ).fetchone()[0]
            pending_import_count = connection.execute(
                "SELECT COUNT(*) FROM pending_imports"
            ).fetchone()[0]
        result = {row["status"]: row["count"] for row in rows}
        result["dataset"] = dataset_count
        result["pending_import"] = pending_import_count
        return result

    def distribution_stats(self) -> dict[str, list[dict[str, object]]]:
        with self.connect() as connection:
            stats: dict[str, list[dict[str, object]]] = {}
            for key in ("month", "season", "geo_cell", "satellite"):
                rows = connection.execute(
                    f"""
                    SELECT {key} AS category, COUNT(*) AS count
                    FROM scenes GROUP BY {key}
                    ORDER BY count DESC, category
                    """
                ).fetchall()
                stats[f"indexed_{key}"] = [dict(row) for row in rows]
            stats["indexed_total"] = [
                {
                    "category": "total",
                    "count": connection.execute(
                        "SELECT COUNT(*) FROM scenes"
                    ).fetchone()[0],
                }
            ]
            for key in (
                "month",
                "season",
                "geo_cell",
                "satellite",
                "dataset_split",
                "label_type",
            ):
                rows = connection.execute(
                    f"""
                    SELECT {key} AS category, COUNT(*) AS count
                    FROM dataset_items GROUP BY {key}
                    ORDER BY count DESC, category
                    """
                ).fetchall()
                stats[key] = [dict(row) for row in rows]
            stats["total"] = [
                {
                    "category": "total",
                    "count": connection.execute(
                        "SELECT COUNT(*) FROM dataset_items"
                    ).fetchone()[0],
                }
            ]
        return stats

    def choose_balanced_split(
        self, scene: Scene, validation_ratio: float
    ) -> str:
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT dataset_split FROM dataset_items WHERE scene_id=?",
                (scene.scene_id,),
            ).fetchone()
            if existing:
                return existing["dataset_split"]
            total, validation = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN dataset_split='val' THEN 1 ELSE 0 END) AS validation
                FROM dataset_items
                """
            ).fetchone()
            group_total, group_validation = connection.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN dataset_split='val' THEN 1 ELSE 0 END) AS validation
                FROM dataset_items
                WHERE geo_cell=? AND month=? AND season=?
                """,
                (scene.geo_cell, scene.month, scene.season),
            ).fetchone()
        validation = validation or 0
        group_validation = group_validation or 0
        overall_needs_validation = validation < (total + 1) * validation_ratio
        group_needs_validation = group_validation < (
            group_total + 1
        ) * validation_ratio
        return "val" if overall_needs_validation and group_needs_validation else "train"

    def export_manifest(self, output_path: Path) -> None:
        with self.connect() as connection:
            rows = connection.execute(
                """
                SELECT scene_id, satellite, dataset_split, image_path, mask_path,
                       source_method, label_type, center_lon, center_lat, month, season,
                       geo_cell, corners_json, created_at
                FROM dataset_items ORDER BY created_at
                """
            ).fetchall()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(dict(row), ensure_ascii=False) + "\n")


def choose_split(scene_id: str, validation_ratio: float) -> str:
    digest = hashlib.sha1(scene_id.encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big") / 2**32
    return "val" if value < validation_ratio else "train"


def safe_path_component(value: object, fallback: str = "unknown") -> str:
    text = str(value or fallback).strip() or fallback
    return "".join("_" if character in '<>:"/\\|?*' else character for character in text)


def normalize_dataset_split(value: object) -> str:
    text = str(value or "train").strip().lower()
    if text in {"val", "validation", "valid", "test"}:
        return "val"
    return "train"


def copy_file_if_missing(source: Path, target: Path) -> bool:
    if path_exists(target):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if source.resolve() == target.resolve():
            return False
    except OSError:
        pass
    shutil.copy2(filesystem_path(source), filesystem_path(target))
    return True


def dataset_record_stem(record: dict[str, object]) -> str:
    for key in ("id", "image_id", "scene_id"):
        value = str(record.get(key) or "").strip()
        if value:
            return safe_path_component(Path(value).stem)
    image_value = str(record.get("jpg_path") or "").strip()
    if image_value:
        return safe_path_component(Path(image_value).stem)
    digest = hashlib.sha1(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
    return digest[:16]


def normalize_dataset_mask_encoding(value: str | None) -> str:
    key = (value or "binary255").strip().lower()
    aliases = {
        "255": "binary255",
        "binary_255": "binary255",
        "binary": "binary255",
        "class": "class_values",
        "classes": "class_values",
        "multi": "class_values",
        "multiclass": "class_values",
        "copy": "preserve",
        "source": "preserve",
    }
    key = aliases.get(key, key)
    return key if key in {"binary255", "class_values", "preserve"} else "binary255"


def write_dataset_image(
    source: Path, target: Path, size: tuple[int, int] = (512, 512)
) -> bool:
    if path_exists(target):
        return False
    save_resampled_image(source, target, size)
    return True


def write_dataset_mask(
    source: Path,
    target: Path,
    encoding: str,
    size: tuple[int, int] = (512, 512),
) -> bool:
    if path_exists(target):
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = normalize_dataset_mask_encoding(encoding)
    with open_image_file(source) as image:
        mask = image.convert("L")
        if mask.size != size:
            mask = mask.resize(size, Image.Resampling.NEAREST)
    if mode == "binary255":
        mask = mask.point(lambda value: 255 if value else 0)
    elif mode == "class_values":
        known_values = set(LABEL_VALUES.values())
        lookup = [
            0 if value == 0 else value if value in known_values else LABEL_VALUES["cloud"]
            for value in range(256)
        ]
        mask = mask.point(lookup)
    mask.save(target, compression="tiff_lzw")
    return True


def archive_import_item(
    scene: Scene,
    mask_path: str,
    method: str,
    config: AppConfig,
    store: ReviewStore,
    external_recorder: Callable[[Scene, str, str, str, str], None] | None = None,
    image_source_path: str | None = None,
    label_type: str = "cloud",
) -> tuple[str, str, str]:
    split = store.choose_balanced_split(scene, config.validation_ratio)
    day = scene.acquired_date.replace("-", "") or date.today().strftime("%Y%m%d")
    root = (
        Path(config.database_dir).expanduser().resolve()
        / "DataCG"
        / day
    )
    image_dir = root / "rgb"
    mask_dir = root / "label"
    image_dir.mkdir(parents=True, exist_ok=True)
    mask_dir.mkdir(parents=True, exist_ok=True)
    source_image = Path(image_source_path or scene.image_path)
    source_mask = Path(mask_path)
    image_target = image_dir / source_image.name
    mask_target = mask_dir / source_mask.name
    output_size = fixed_square_size(config)
    save_resampled_image(source_image, image_target, output_size)
    save_resampled_mask(
        source_mask,
        mask_target,
        output_size,
        label_type=label_type,
        materialize_label=True,
        mask_encoding=config.dataset_mask_encoding,
    )
    try:
        if external_recorder:
            external_recorder(
                scene, split, str(image_target), str(mask_target), method
            )
    except Exception:
        image_target.unlink(missing_ok=True)
        mask_target.unlink(missing_ok=True)
        raise
    store.add_dataset_item(
        scene, split, str(image_target), str(mask_target), method, label_type
    )
    store.export_manifest(Path(config.database_dir) / "archive_manifest.jsonl")
    return split, str(image_target), str(mask_target)


def export_records_to_dataset(
    records: Iterable[dict[str, object]],
    output_dir: str | Path,
    mask_encoding: str = "binary255",
    output_size: tuple[int, int] = (512, 512),
) -> dict[str, object]:
    root = Path(output_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    encoding = normalize_dataset_mask_encoding(mask_encoding)
    stats: dict[str, object] = {
        "total": 0,
        "copied": 0,
        "existing": 0,
        "missing": 0,
        "failed": 0,
        "failures": [],
        "manifest_path": str(root / "manifest.jsonl"),
        "mask_encoding": encoding,
        "layout": "split_rgb_label_id_tif",
    }
    manifest_rows: list[dict[str, object]] = []
    failures: list[str] = []
    for record in records:
        stats["total"] = int(stats["total"]) + 1
        image_value = str(record.get("jpg_path") or "").strip()
        mask_value = str(record.get("label_path") or "").strip()
        image_source = Path(image_value) if image_value else None
        mask_source = Path(mask_value) if mask_value else None
        if (
            image_source is None
            or mask_source is None
            or not path_is_file(image_source)
            or not path_is_file(mask_source)
        ):
            stats["missing"] = int(stats["missing"]) + 1
            identity = record.get("image_id") or record.get("id") or image_value
            failures.append(f"{identity}: 源图像或标签不存在")
            continue
        split = normalize_dataset_split(record.get("tra_val"))
        stem = dataset_record_stem(record)
        image_target = root / split / "rgb" / f"{stem}.tif"
        mask_target = root / split / "label" / f"{stem}.tif"
        try:
            image_copied = write_dataset_image(image_source, image_target, output_size)
            mask_copied = write_dataset_mask(
                mask_source, mask_target, encoding, output_size
            )
            if image_copied or mask_copied:
                stats["copied"] = int(stats["copied"]) + 1
            else:
                stats["existing"] = int(stats["existing"]) + 1
            row = dict(record)
            row.update(
                {
                    "dataset_split": split,
                    "dataset_image_path": str(image_target),
                    "dataset_mask_path": str(mask_target),
                    "dataset_mask_encoding": encoding,
                }
            )
            manifest_rows.append(row)
        except Exception as exc:
            stats["failed"] = int(stats["failed"]) + 1
            identity = record.get("image_id") or record.get("id") or image_value
            failures.append(f"{identity}: {exc}")
    stats["failures"] = failures[:100]
    manifest_path = Path(str(stats["manifest_path"]))
    with manifest_path.open("w", encoding="utf-8") as handle:
        for row in manifest_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return stats


def copy_to_dataset(
    scene: Scene,
    mask_path: str,
    method: str,
    config: AppConfig,
    store: ReviewStore,
    external_recorder: Callable[[Scene, str, str, str, str], None] | None = None,
    image_source_path: str | None = None,
    label_type: str = "cloud",
) -> tuple[str, str, str]:
    split = store.choose_balanced_split(scene, config.validation_ratio)
    root = Path(config.database_dir).expanduser().resolve() / split / scene.satellite
    image_dir = root / "images"
    mask_dir = root / "masks"
    image_dir.mkdir(parents=True, exist_ok=True)
    mask_dir.mkdir(parents=True, exist_ok=True)
    source_image = Path(image_source_path or scene.image_path)
    source_mask = Path(mask_path)
    image_target = image_dir / f"{scene.scene_id}{source_image.suffix.lower()}"
    mask_target = mask_dir / f"{scene.scene_id}_mask.tif"
    output_size = fixed_square_size(config)
    save_resampled_image(source_image, image_target, output_size)
    save_resampled_mask(
        source_mask,
        mask_target,
        output_size,
        label_type=label_type,
        materialize_label=True,
        mask_encoding=config.dataset_mask_encoding,
    )
    try:
        if external_recorder:
            external_recorder(
                scene, split, str(image_target), str(mask_target), method
            )
    except Exception:
        image_target.unlink(missing_ok=True)
        mask_target.unlink(missing_ok=True)
        raise
    store.add_dataset_item(
        scene, split, str(image_target), str(mask_target), method, label_type
    )
    store.export_manifest(Path(config.database_dir) / "manifest.jsonl")
    return split, str(image_target), str(mask_target)


def copy_to_pending_import(
    scene: Scene,
    mask_path: str,
    method: str,
    note: str,
    config: AppConfig,
    store: ReviewStore,
    label_type: str = "cloud",
) -> tuple[str, str]:
    root = (
        Path(config.cache_dir).expanduser().resolve()
        / "pending_import"
        / scene.scene_id
    )
    root.mkdir(parents=True, exist_ok=True)
    source_image = Path(scene.image_path)
    source_mask = Path(mask_path)
    image_target = root / f"{scene.scene_id}{source_image.suffix.lower()}"
    mask_target = root / f"{scene.scene_id}_candidate_mask.tif"
    output_size = fixed_square_size(config)
    save_resampled_image(source_image, image_target, output_size)
    save_resampled_mask(
        source_mask,
        mask_target,
        output_size,
        label_type=label_type,
        materialize_label=True,
        mask_encoding=config.dataset_mask_encoding,
    )
    store.stage_import(
        scene,
        str(image_target),
        str(mask_target),
        method,
        note,
        label_type,
    )
    return str(image_target), str(mask_target)


def cache_review_scene(
    scene: Scene,
    config: AppConfig,
    folder_suffix: str = "",
    root_override: str | Path | None = None,
) -> str:
    if root_override:
        root = Path(root_override).expanduser().resolve()
    else:
        today_folder = datetime.now().strftime("%m%d")
        suffix = safe_path_component(folder_suffix, "").strip("_")
        if suffix:
            today_folder = f"{today_folder}_{suffix}"
        root = (
            Path(config.cache_dir).expanduser().resolve()
            / "review"
            / today_folder
            / safe_path_component(scene.scene_id)
        )
    root.mkdir(parents=True, exist_ok=True)
    output_size = fixed_square_size(config)
    files: dict[str, str] = {}
    candidates: list[tuple[str, str]] = [
        ("image", scene.image_path),
    ]
    for method, path in scene.masks.items():
        candidates.append((method, path))
    for label, source in candidates:
        if not source:
            continue
        source_path = Path(source)
        if not path_is_file(source_path):
            continue
        target = root / f"{safe_path_component(label)}{source_path.suffix.lower()}"
        try:
            same_file = source_path.resolve(strict=False) == target.resolve(strict=False)
        except OSError:
            same_file = False
        if not same_file:
            if label == "image":
                save_resampled_image(source_path, target, output_size)
            else:
                save_resampled_mask(source_path, target, output_size)
        files[label] = str(target)
    manifest = {
        "scene_id": scene.scene_id,
        "satellite": scene.satellite,
        "acquired_date": scene.acquired_date,
        "status": scene.status,
        "difference": scene.difference,
        "difference_percent": scene.difference_percent,
        "iou": scene.iou,
        "cloud_ratios": scene.cloud_ratios,
        "source_paths": {
            "image": scene.image_path,
            "source_tif": scene.source_tif_path,
            "masks": scene.masks,
        },
        "cached_files": files,
        "cache_size": output_size,
        "cached_at": datetime.now().isoformat(timespec="seconds"),
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if files.get("image"):
        scene.image_path = files["image"]
    for method in list(scene.masks):
        if files.get(method):
            scene.masks[method] = files[method]
    scene.cloud_ratios["review_cache_path"] = str(root)
    return str(root)


def delete_pending_files(item: dict[str, object]) -> None:
    paths = [
        Path(str(item[key]))
        for key in ("image_path", "mask_path")
        if item.get(key)
    ]
    for path in paths:
        path.unlink(missing_ok=True)
    parents = {path.parent for path in paths}
    for parent in parents:
        try:
            parent.rmdir()
        except OSError:
            pass


def copy_to_typical(scene: Scene, mask_path: str, config: AppConfig) -> Path:
    target = Path(config.typical_dir).expanduser().resolve() / scene.satellite / scene.scene_id
    target.mkdir(parents=True, exist_ok=True)
    output_size = fixed_square_size(config)
    save_resampled_image(
        scene.image_path,
        target / Path(scene.image_path).name,
        output_size,
    )
    save_resampled_mask(
        mask_path,
        target / Path(mask_path).name,
        output_size,
    )
    return target


def cleanup_cache(config: AppConfig) -> tuple[int, int]:
    cache = Path(config.cache_dir).expanduser()
    if not cache.exists():
        return 0, 0
    review_root = (cache / "review").resolve()

    def is_review_cache(path: Path) -> bool:
        try:
            path.resolve().relative_to(review_root)
            return True
        except ValueError:
            return False

    files = [
        path for path in cache.rglob("*") if path.is_file() and not is_review_cache(path)
    ]
    removed = 0
    freed = 0
    cutoff = time.time() - config.cache_retention_days * 86400
    for path in files:
        if path.stat().st_mtime < cutoff:
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
            removed += 1
    remaining = sorted(
        (path for path in cache.rglob("*") if path.is_file() and not is_review_cache(path)),
        key=lambda item: item.stat().st_mtime,
    )
    total = sum(path.stat().st_size for path in remaining)
    maximum = config.cache_max_mb * 1024 * 1024
    for path in remaining:
        if total <= maximum:
            break
        size = path.stat().st_size
        path.unlink(missing_ok=True)
        total -= size
        freed += size
        removed += 1
    return removed, freed
