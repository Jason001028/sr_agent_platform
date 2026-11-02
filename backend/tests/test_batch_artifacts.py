"""PR D1/D2 acceptance: DetectionResult and MaskResult contracts.

These tests use only temporary directories and synthetic TIFFs; they never
touch a real SR run, GPU, disk array or production image.  The real detector
channel is exercised on tiny synthetic PAN/NOSR files (CPU only); the mock
center detector remains the demo channel for the pipeline.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import zlib

import numpy as np
from PIL import Image, ImageDraw, TiffImagePlugin

from backend.services import batch_contracts, batch_detector, batch_mask
from backend.services import batch_worker, preview_jpg


def target(width=37, height=23, item_id="item-1", image_key="pan-key",
           revision="rev-1"):
    return {
        "item_id": item_id,
        "image_key": image_key,
        "revision": revision,
        "width": width,
        "height": height,
        "path": "PAN.tif",
    }


def write_predictor_tiff(path, array, *, predictor=2):
    """Write one-strip little-endian TIFF using ``predictor`` on disk.

    Pillow ignores the Predictor option when saving, so build the classic TIFF
    by hand: this is the on-disk representation a real Deflate+Predictor=2
    producer (for example GDAL) would write, and it lets the reader be compared
    with Pillow/tifffile as independent decoders.
    """
    array = np.asarray(array)
    if array.ndim != 2:
        raise ValueError("write_predictor_tiff expects a 2-D array")
    height, width = array.shape
    bits = array.dtype.itemsize * 8
    sample_format = (3 if array.dtype.kind == "f"
                     else 2 if array.dtype.kind == "i" else 1)
    encoded = array.copy()
    if predictor == 2:
        encoded[:, 1:] = array[:, 1:] - array[:, :-1]
    raw = encoded.astype(array.dtype.newbyteorder("<"), copy=False).tobytes()
    data = zlib.compress(raw)
    entries = [
        (256, 4, width),          # ImageWidth
        (257, 4, height),         # ImageLength
        (258, 3, bits),           # BitsPerSample
        (259, 3, 8),              # Compression = Deflate
        (262, 3, 1),              # Photometric = MinIsBlack
        (273, 4, 146),            # StripOffsets: after header + IFD
        (277, 3, 1),              # SamplesPerPixel
        (278, 4, height),         # RowsPerStrip
        (279, 4, len(data)),      # StripByteCounts
        (317, 3, predictor),      # Predictor
        (339, 3, sample_format),  # SampleFormat
    ]
    out = bytearray(b"II" + struct.pack("<H", 42) + struct.pack("<I", 8))
    out += struct.pack("<H", len(entries))
    for tag, kind, value in entries:
        out += struct.pack("<HHI", tag, kind, 1)
        out += (struct.pack("<I", value) if kind == 4
                else struct.pack("<H", value) + b"\0\0")
    out += struct.pack("<I", 0)
    assert len(out) == 146, len(out)
    Path(path).write_bytes(bytes(out) + data)
    return Path(path)


class DetectionContractTests(unittest.TestCase):
    def test_center_detection_returns_the_frozen_contract(self):
        detection = batch_detector.detect(target())
        self.assertEqual(
            detection["contract_version"],
            batch_contracts.DETECTION_CONTRACT_VERSION)
        self.assertEqual(
            detection["coordinate_space"], batch_contracts.COORDINATE_SPACE)
        self.assertEqual(detection["item_id"], "item-1")
        self.assertEqual(detection["image_id"], "pan-key")
        self.assertEqual(detection["image_size"], {"width": 37, "height": 23})
        self.assertEqual(detection["provenance"], "mock")
        self.assertEqual(detection["detector_name"], "deterministic-center")
        self.assertEqual(detection["candidate_count"], 1)
        candidate = detection["candidates"][0]
        self.assertEqual(candidate["kind"], "bbox")
        x0, y0, x1, y1 = candidate["bbox"]
        self.assertTrue(0 <= x0 <= x1 <= 36)
        self.assertTrue(0 <= y0 <= y1 <= 22)

    def test_none_mode_is_an_empty_success_and_error_mode_raises(self):
        empty = batch_detector.detect(target(), config={"mode": "none"})
        self.assertEqual(empty["candidates"], [])
        self.assertEqual(empty["candidate_count"], 0)
        self.assertEqual(empty["provenance"], "mock")
        self.assertIn(
            "no_candidate", [item["code"] for item in empty["diagnostics"]])
        for mode in ("error", "unknown-mode"):
            with self.assertRaises(batch_detector.DetectionError):
                batch_detector.detect(target(), config={"mode": mode})

    def test_out_of_bounds_bbox_is_clipped_and_fully_outside_is_rejected(self):
        clipped, diagnostics = batch_contracts.normalize_candidates(
            [{"bbox": [-3, -2, 5, 6]}], 16, 12)
        self.assertEqual(clipped[0]["bbox"], [0, 0, 5, 6])
        self.assertEqual(diagnostics[0]["code"], "candidate_clipped")
        with self.assertRaises(batch_contracts.ContractError):
            batch_contracts.normalize_candidates(
                [{"bbox": [20, 20, 30, 30]}], 16, 12)

    def test_degenerate_polygon_nan_and_reserved_kinds_are_rejected(self):
        bad_cases = [
            {"kind": "polygon", "points": [[1, 1], [2, 2], [3, 3]]},
            {"kind": "polygon", "points": [[1, 1], [2, 2]]},
            {"kind": "polygon", "points": [[1, 1], [2, 2], [math.nan, 3]]},
            {"kind": "polygon", "points": [[1, 1], [2, 2], [math.inf, 3]]},
            {"kind": "artifact", "bbox": [1, 1, 3, 3]},
            {"kind": "local_blur", "bbox": [1, 1, 3, 3]},
        ]
        for case in bad_cases:
            with self.assertRaises(batch_contracts.ContractError):
                batch_contracts.normalize_candidates([case], 16, 12)

    def test_polygon_keeps_original_geometry_and_clips_only_metadata(self):
        """P1 3：逐顶点夹紧会改掉形状（10×10 三角 36 px → 21 px）。

        提交给栅格化的仍是检测器原始顶点；只有元数据 bbox 被裁到画布内，
        诊断里同时保留 `from`/`to`，便于复核“裁掉了什么”。
        """
        clipped, diagnostics = batch_contracts.normalize_candidates(
            [{"kind": "polygon",
              "points": [[-2, 1], [4, 1], [4, 5], [-2, 5]]}], 16, 12)
        self.assertEqual(
            clipped[0]["points"], [[-2, 1], [4, 1], [4, 5], [-2, 5]])
        self.assertEqual(clipped[0]["bbox"], [0, 1, 4, 5])
        self.assertEqual(diagnostics[0]["code"], "polygon_clipped")
        self.assertEqual(
            diagnostics[0]["from"], [[-2, 1], [4, 1], [4, 5], [-2, 5]])
        self.assertEqual(
            diagnostics[0]["to"], [[0, 1], [4, 1], [4, 5], [0, 5]])

    def test_detection_result_rejects_wrong_size_and_provenance(self):
        detection = batch_detector.detect(target())
        with self.assertRaises(batch_contracts.ContractError):
            batch_contracts.validate_detection_result(
                detection, width=38, height=23)
        real = {**detection, "provenance": "real"}
        # demo 允许真实 CPU 检测（provenance 如实为 real）；production 拒绝 mock。
        batch_contracts.validate_detection_result(real, mode="demo")
        with self.assertRaises(batch_contracts.DetectionError):
            batch_contracts.validate_detection_result(
                detection, mode="production")
        with self.assertRaises(batch_contracts.ContractError):
            batch_contracts.validate_detection_result(
                {**detection, "provenance": "bogus"}, mode="demo")

    def test_worker_accepts_real_in_demo_and_rejects_mock_in_production(self):
        real = {**batch_detector.detect(target()), "provenance": "real"}
        with tempfile.TemporaryDirectory(prefix="d2-real-") as tmp:
            mask_path = Path(tmp) / "mask.tif"
            with mock.patch.object(
                    batch_worker.batch_detector, "detect", return_value=real):
                payload = batch_worker._detection_job({
                    "target_image": target(),
                    "mask_path": mask_path.as_posix(),
                    "mode": "demo",
                })
        self.assertEqual(payload["detection"]["provenance"], "real")
        self.assertIsNone(batch_worker._detection_provenance_gate(real, "demo"))
        with self.assertRaises(batch_contracts.DetectionError):
            batch_worker._detection_provenance_gate(
                batch_detector.detect(target()), "production")


class MaskContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="d2-mask-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_non_square_bottom_right_one_pixel_box(self):
        out = self.root / "corner.tif"
        meta = batch_mask.build_mask(
            out, width=37, height=23,
            candidates=[{"bbox": [36, 22, 36, 22]}], provenance="mock")
        self.assertEqual(meta["foreground_pixels"], 1)
        with Image.open(out) as image:
            self.assertEqual(image.size, (37, 23))
            self.assertEqual(image.mode, "L")
            self.assertEqual(image.getpixel((36, 22)), 255)
            self.assertEqual(image.getpixel((35, 22)), 0)
            self.assertEqual(image.getpixel((36, 21)), 0)
        checked = batch_mask.verify_mask(
            out, width=37, height=23, sha256=meta["sha256"])
        self.assertEqual(checked["foreground_pixels"], 1)
        self.assertEqual(
            checked["coordinate_space"], batch_contracts.COORDINATE_SPACE)

    def test_empty_and_fully_out_of_bounds_candidates_are_rejected(self):
        out = self.root / "bad.tif"
        with self.assertRaises(batch_mask.MaskError):
            batch_mask.build_mask(
                out, width=37, height=23, candidates=[], provenance="mock")
        with self.assertRaises(batch_mask.MaskError):
            batch_mask.build_mask(
                out, width=37, height=23,
                candidates=[{"bbox": [40, 40, 50, 50]}], provenance="mock")
        self.assertFalse(out.exists())

    def test_writer_allocates_one_strip_at_a_time(self):
        out = self.root / "strips.tif"
        sizes = []
        real_new = Image.new

        def spy(mode, size, color=0):
            sizes.append(size)
            return real_new(mode, size, color)

        with mock.patch.object(Image, "new", side_effect=spy):
            meta = batch_mask.build_mask(
                out, width=257, height=130,
                candidates=[{"bbox": [0, 0, 256, 129]}],
                provenance="mock", rows_per_strip=64)
        self.assertEqual(meta["foreground_pixels"], 257 * 130)
        self.assertEqual(meta["strip_count"], math.ceil(130 / 64))
        self.assertTrue(sizes)
        self.assertTrue(all(height <= 64 for _, height in sizes), sizes)
        checked = batch_mask.verify_mask(out, width=257, height=130)
        self.assertEqual(checked["strip_count"], math.ceil(130 / 64))
        self.assertEqual(checked["compression"], "deflate")

    def test_half_written_mask_is_not_published(self):
        out = self.root / "half.tif"
        with mock.patch.object(
                batch_mask, "_verify_stream_tiff",
                side_effect=batch_mask.MaskError("simulated verification")):
            with self.assertRaises(batch_mask.MaskError):
                batch_mask.build_mask(
                    out, width=37, height=23,
                    candidates=[{"bbox": [1, 1, 5, 5]}], provenance="mock")
        self.assertFalse(out.exists())
        self.assertFalse(out.with_name("half.tmp.tif").exists())

    def test_polygon_candidate_rasterizes_inside_the_mask(self):
        out = self.root / "polygon.tif"
        meta = batch_mask.build_mask(
            out, width=37, height=23,
            candidates=[{
                "points": [[2, 2], [20, 2], [20, 18], [2, 18]],
            }],
            provenance="mock")
        self.assertGreater(meta["foreground_pixels"], 0)
        with Image.open(out) as image:
            self.assertEqual(image.getpixel((10, 10)), 255)
            self.assertEqual(image.getpixel((1, 1)), 0)
            self.assertEqual(image.getpixel((30, 20)), 0)

    def test_overlay_block_max_keeps_the_bottom_right_pixel(self):
        pan = target()
        detection = batch_detector.detect(pan)
        detection = {
            **detection,
            "candidates": [{
                "candidate_id": "corner",
                "kind": "bbox",
                "bbox": [36, 22, 36, 22],
                "points": None,
                "score": None,
                "label": "corner",
            }],
        }
        path = self.root / "artifacts" / "item-1" / "detect-1" / "mask.tif"
        result = batch_mask.build_mask_result(
            detection, pan, {"path": path, "overlay_max_edge": 16})
        self.assertEqual(result["foreground_pixels"], 1)
        with Image.open(path.parent / "overlay.png") as overlay:
            scale = result["overlay"]["scale"]
            self.assertEqual(overlay.getpixel((36 // scale, 22 // scale)), 255)

    def test_legacy_pillow_mask_still_verifies(self):
        out = self.root / "legacy.tif"
        Image.new("L", (16, 12), 255).save(out)
        checked = batch_mask.verify_mask(out, width=16, height=12)
        self.assertEqual(checked["foreground_pixels"], 16 * 12)
        self.assertEqual(checked["width"], 16)
        self.assertEqual(checked["height"], 12)

    def test_tampered_mask_values_and_digest_are_rejected(self):
        tampered = self.root / "tampered.tif"
        Image.new("L", (16, 12), 7).save(tampered)
        with self.assertRaises(batch_mask.MaskError):
            batch_mask.verify_mask(tampered, width=16, height=12)

        out = self.root / "valid.tif"
        meta = batch_mask.build_mask(
            out, width=16, height=12,
            candidates=[{"bbox": [2, 2, 5, 5]}], provenance="mock")
        with self.assertRaises(batch_mask.MaskError):
            batch_mask.verify_mask(
                out, width=16, height=12, sha256="0" * 64)
        batch_mask.verify_mask(
            out, width=16, height=12, sha256=meta["sha256"])

    def test_build_mask_result_publishes_mask_overlay_and_manifests(self):
        pan = target()
        detection = batch_detector.detect(pan)
        path = self.root / "artifacts" / "item-1" / "detect-1" / "mask.tif"
        result = batch_mask.build_mask_result(
            detection, pan, {"path": path, "overlay_max_edge": 16})

        self.assertEqual(result["contract_version"], "mask.v1")
        self.assertEqual(result["item_id"], "item-1")
        self.assertEqual(result["source_image_key"], "pan-key")
        self.assertEqual(result["source_revision"], "rev-1")
        self.assertEqual(result["provenance"], "mock")
        self.assertEqual(result["detector_name"], "deterministic-center")
        self.assertEqual(result["detection_contract_version"], "detection.v1")
        self.assertEqual(
            result["revision"], preview_jpg.source_revision(path))

        for name in ("mask.tif", "overlay.png", "mask.json", "overlay.json"):
            self.assertTrue((path.parent / name).is_file(), name)
        overlay_path = path.parent / "overlay.png"
        self.assertEqual(result["overlay"]["path"], overlay_path.as_posix())
        self.assertEqual(
            result["overlay"]["revision"],
            preview_jpg.source_revision(overlay_path))
        self.assertEqual(
            result["overlay"]["scale"],
            max(1, math.ceil(max(37, 23) / 16)))

        mask_doc = json.loads(
            (path.parent / "mask.json").read_text(encoding="utf-8"))
        self.assertEqual(mask_doc["mask"]["file"], "mask.tif")
        self.assertEqual(mask_doc["source"]["revision"], "rev-1")
        self.assertEqual(mask_doc["detection"]["provenance"], "mock")
        self.assertEqual(mask_doc["builder"]["name"], "strip-mask-builder")
        self.assertEqual(
            mask_doc["mask"]["coordinate_space"], "source_pixel")
        overlay_doc = json.loads(
            (path.parent / "overlay.json").read_text(encoding="utf-8"))
        self.assertEqual(overlay_doc["overlay"]["file"], "overlay.png")
        self.assertEqual(
            overlay_doc["image_size"], {"width": 37, "height": 23})
        self.assertEqual(len(overlay_doc["candidates"]), 1)

        checked = batch_mask.verify_mask(
            path, width=37, height=23, sha256=result["sha256"])
        self.assertEqual(
            checked["foreground_pixels"], result["foreground_pixels"])

    def test_build_mask_result_rejects_wrong_item_and_reuses_the_same_bundle(self):
        pan = target()
        detection = batch_detector.detect(pan)
        path = self.root / "artifacts" / "item-1" / "detect-1" / "mask.tif"
        with self.assertRaises(batch_mask.MaskError):
            batch_mask.build_mask_result(
                detection, {**pan, "item_id": "other"}, {"path": path})
        self.assertFalse(path.parent.exists())

        first = batch_mask.build_mask_result(detection, pan, {"path": path})
        second = batch_mask.build_mask_result(detection, pan, {"path": path})
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(second["sha256"], first["sha256"])
        self.assertIn(
            "mask_bundle_reused",
            [d["code"] for d in second["diagnostics"]])

    def test_build_mask_result_cleans_up_after_a_manifest_failure(self):
        pan = target()
        detection = batch_detector.detect(pan)
        path = self.root / "artifacts" / "item-1" / "detect-1" / "mask.tif"
        with mock.patch.object(
                batch_mask, "_write_json",
                side_effect=[None, batch_mask.MaskError("simulated")]):
            with self.assertRaises(batch_mask.MaskError):
                batch_mask.build_mask_result(detection, pan, {"path": path})
        self.assertFalse(path.parent.exists())
        # 轮次锁文件是跨进程互斥的锚点，故意不删（删掉会造成 unlink 竞争），
        # 但不允许残留半成品目录或其它工件。
        leftovers = [p.name for p in path.parent.parent.iterdir()
                     if not p.name.endswith(".lock")]
        self.assertEqual(leftovers, [])
        self.assertTrue(
            path.parent.with_name(path.parent.name + ".lock").is_file())

    def test_mask_result_contract_rejects_zero_foreground(self):
        pan = target()
        detection = batch_detector.detect(pan)
        path = self.root / "artifacts" / "item-1" / "detect-1" / "mask.tif"
        result = batch_mask.build_mask_result(
            detection, pan, {"path": path, "overlay_max_edge": 16})
        bad = {**result, "foreground_pixels": 0}
        with self.assertRaises(batch_contracts.ContractError):
            batch_contracts.validate_mask_result(bad)

    def test_submit_boundary_rejects_mask_provenance_mismatch(self):
        item = {
            "item_id": "item-1",
            "version": 1,
            "checkpoint": {
                "target_image": target(),
                "detection": {
                    "contract_version": "detection.v1",
                    "item_id": "item-1",
                    "provenance": "mock",
                },
                "mask": {
                    "path": "unused/mask.tif",
                    "contract_version": "mask.v1",
                    "coordinate_space": "source_pixel",
                    "dtype": "uint8",
                    "item_id": "item-1",
                    "provenance": "real",
                },
            },
        }
        with mock.patch.object(batch_worker, "_open_item"):
            with self.assertRaises(batch_worker.ItemBlocked):
                batch_worker._verify_submission_inputs(item, "demo")

    def test_worker_detection_job_returns_a_published_mask_result(self):
        pan = target()
        path = self.root / "artifacts" / "item-1" / "detect-1" / "mask.tif"
        payload = batch_worker._detection_job({
            "target_image": pan,
            "mask_path": path.as_posix(),
            "mode": "demo",
            "rows_per_strip": 8,
            "overlay_max_edge": 16,
        })
        mask = payload["mask"]
        self.assertEqual(mask["contract_version"], "mask.v1")
        self.assertEqual(mask["coordinate_space"], "source_pixel")
        self.assertEqual(mask["dtype"], "uint8")
        self.assertEqual(mask["provenance"], "mock")
        self.assertGreater(mask["foreground_pixels"], 0)
        self.assertTrue(path.is_file())
        self.assertTrue((path.parent / "overlay.png").is_file())
        self.assertEqual(payload["detection"]["item_id"], "item-1")
        # `mask["candidates"]` stays an int for PR C consumers.
        self.assertEqual(mask["candidates"], 1)
        self.assertEqual(mask["candidate_count"], 1)


class RealDetectorTests(unittest.TestCase):
    """P1 1: the real channel reads PAN pixels, bounded and in source space."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="d1-real-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _write(self, name, array, **save):
        path = self.root / name
        Image.fromarray(array).save(path, format="TIFF", **save)
        return path

    def _ref(self, path, width, height, key):
        return {
            "item_id": "item-1",
            "path": path.as_posix(),
            "image_key": key,
            "revision": preview_jpg.source_revision(path),
            "width": width,
            "height": height,
        }

    def _pan(self, width=64, height=24, flat_row=9, value=4242, nodata=False):
        array = (np.arange(width * height, dtype=np.uint16)
                 .reshape(height, width) % 997)
        array[flat_row] = value
        save = {}
        if nodata:
            ifd = TiffImagePlugin.ImageFileDirectory_v2()
            ifd[42113] = "0"
            save["tiffinfo"] = ifd
        return array, self._write("PAN.tif", array, **save)

    def _detect(self, pan, width=64, height=24, **config):
        nosr = self._write("NOSR.tif", np.full((12, 32), 7, dtype=np.uint16))
        return batch_detector.detect(
            self._ref(pan, width, height, "pan-key"),
            self._ref(nosr, 32, 12, "nosr-key"),
            {"mode": "real", "execution_mode": "demo", **config},
        )

    def test_reads_actual_pixels_and_reports_source_pixel_boxes(self):
        _array, pan = self._pan()
        result = self._detect(pan, window_rows=8)
        self.assertEqual(result["provenance"], "real")
        self.assertEqual(result["detector_name"], batch_detector.REAL_DETECTOR_NAME)
        self.assertEqual(result["window_rows"], 8)
        self.assertEqual(result["reference_image_key"], "nosr-key")
        labels = {(c["label"], tuple(c["bbox"])) for c in result["candidates"]}
        self.assertIn(("flat_row", (0, 9, 63, 9)), labels)
        self.assertFalse(any(label == "flat_column" for label, _ in labels))
        for candidate in result["candidates"]:
            x0, y0, x1, y1 = candidate["bbox"]
            self.assertTrue(0 <= x0 <= x1 <= 63)
            self.assertTrue(0 <= y0 <= y1 <= 23)
        scan = next(d for d in result["diagnostics"]
                    if d["code"] == "flatline_scan")
        self.assertEqual(scan["flat_rows"], 1)

    def test_nodata_rows_do_not_become_findings(self):
        array = (np.arange(64 * 20, dtype=np.uint16).reshape(20, 64) % 997)
        array[5] = 0          # NoData fill: empty area, not a stuck line
        array[12] = 777       # genuine flat line
        ifd = TiffImagePlugin.ImageFileDirectory_v2()
        ifd[42113] = "0"
        pan = self._write("PAN.tif", array, tiffinfo=ifd)
        nosr = self._write("NOSR.tif", np.full((10, 32), 7, dtype=np.uint16))
        result = batch_detector.detect(
            self._ref(pan, 64, 20, "pan-key"),
            self._ref(nosr, 32, 10, "nosr-key"),
            {"mode": "real", "execution_mode": "demo"})
        labels = {(c["label"], tuple(c["bbox"])) for c in result["candidates"]}
        self.assertIn(("flat_row", (0, 12, 63, 12)), labels)
        self.assertNotIn(("flat_row", (0, 5, 63, 5)), labels)
        scan = next(d for d in result["diagnostics"]
                    if d["code"] == "flatline_scan")
        self.assertEqual(scan["nodata"], 0.0)

    def test_rejects_stale_revision_and_grid_drift(self):
        _array, pan = self._pan()
        nosr = self._write("NOSR.tif", np.full((12, 32), 7, dtype=np.uint16))
        pan_ref = self._ref(pan, 64, 24, "pan-key")
        nosr_ref = self._ref(nosr, 32, 12, "nosr-key")
        with self.assertRaises(batch_detector.DetectionError):
            batch_detector.detect(
                {**pan_ref, "revision": "stale"},
                nosr_ref, {"mode": "real"})
        with self.assertRaises(batch_detector.DetectionError):
            batch_detector.detect(
                pan_ref, {**nosr_ref, "revision": "stale"}, {"mode": "real"})

        same_size = self._write(
            "same.tif", np.full((24, 64), 7, dtype=np.uint16))
        with self.assertRaises(batch_detector.DetectionError):
            batch_detector.detect(
                pan_ref, self._ref(same_size, 64, 24, "same"), {"mode": "real"})
        larger = self._write(
            "larger.tif", np.full((48, 128), 7, dtype=np.uint16))
        with self.assertRaises(batch_detector.DetectionError):
            batch_detector.detect(
                pan_ref, self._ref(larger, 128, 48, "larger"), {"mode": "real"})
        deeper = self._write(
            "eight-bit.tif", np.full((12, 32), 7, dtype=np.uint8))
        with self.assertRaises(batch_detector.DetectionError):
            batch_detector.detect(
                pan_ref, self._ref(deeper, 32, 12, "eight"), {"mode": "real"})

    def test_rejects_multiband_tiled_and_unreadable_tiffs(self):
        rgb = self._write("rgb.tif", np.zeros((16, 16, 3), dtype=np.uint8))
        nosr = self._write("NOSR.tif", np.full((8, 8), 7, dtype=np.uint8))
        with self.assertRaises(batch_detector.DetectionError):
            batch_detector.detect(
                self._ref(rgb, 16, 16, "rgb"),
                self._ref(nosr, 8, 8, "nosr"), {"mode": "real"})

        gray = self._write("gray.tif", np.zeros((16, 16), dtype=np.uint8))
        tiled = {"W": 16, "H": 16, "bits": 8, "spp": 1,
                 "sample_format": "uint", "compression": "none", "tiled": True}
        with mock.patch.object(preview_jpg, "probe_tiff", return_value=tiled):
            with self.assertRaises(batch_detector.DetectionError):
                batch_detector.detect(
                    self._ref(gray, 16, 16, "gray"),
                    self._ref(nosr, 8, 8, "nosr"), {"mode": "real"})
        with mock.patch.object(
                preview_jpg, "probe_tiff", side_effect=OSError("bad header")):
            with self.assertRaises(batch_detector.DetectionError):
                batch_detector.detect(
                    self._ref(gray, 16, 16, "gray"),
                    self._ref(nosr, 8, 8, "nosr"), {"mode": "real"})

    def test_requires_a_frozen_reference_image(self):
        _array, pan = self._pan()
        with self.assertRaises(batch_detector.DetectionError):
            batch_detector.detect(
                self._ref(pan, 64, 24, "pan-key"), None, {"mode": "real"})

    # ---- P1 1: bounded strip reader -------------------------------------

    def _striped(self, name, array, rows_per_strip=5, **save):
        ifd = TiffImagePlugin.ImageFileDirectory_v2()
        ifd[278] = rows_per_strip
        path = self.root / name
        Image.fromarray(array).save(path, format="TIFF", tiffinfo=ifd, **save)
        return path

    def test_pixel_reader_never_yields_more_than_window_rows(self):
        array = (np.arange(64 * 24, dtype=np.uint16)
                 .reshape(24, 64) % 997)
        pan = self._striped("striped.tif", array, rows_per_strip=5)
        layout = batch_detector._read_strip_layout(pan, 64, 24)
        self.assertEqual(layout["strips"], 5)
        self.assertEqual(layout["compression"], 1)
        windows = list(batch_detector._iter_windows(pan, layout, 64, 24, 8))
        self.assertTrue(windows)
        self.assertTrue(all(w.shape[0] <= 8 for w in windows))
        self.assertTrue(all(w.shape[1] == 64 for w in windows))
        self.assertEqual(sum(w.shape[0] for w in windows), 24)
        np.testing.assert_array_equal(np.vstack(windows), array)

    def test_scan_never_asks_pillow_to_decode_the_whole_image(self):
        _array, pan = self._pan()
        with mock.patch.object(
                Image.Image, "crop",
                side_effect=AssertionError("Pillow crop() 会整图解码")):
            result = self._detect(pan, window_rows=8)
        self.assertEqual(result["provenance"], "real")
        self.assertIn(
            ("flat_row", (0, 9, 63, 9)),
            {(c["label"], tuple(c["bbox"])) for c in result["candidates"]})

    def test_deflate_strips_decode_the_same_pixels(self):
        array = (np.arange(64 * 24, dtype=np.uint16)
                 .reshape(24, 64) % 997)
        array[9] = 4242
        plain = self._striped("plain.tif", array, rows_per_strip=5)
        packed = self._striped(
            "packed.tif", array, rows_per_strip=5,
            compression="tiff_deflate")
        layout = batch_detector._read_strip_layout(packed, 64, 24)
        self.assertEqual(layout["compression"], 8)
        for path in (plain, packed):
            rows_flat, columns_flat, nodata = batch_detector._scan_flat_lines(
                path, 64, 24, 8)
            self.assertIsNone(nodata)
            self.assertEqual(
                [i for i, flat in enumerate(rows_flat) if flat], [9])
            self.assertFalse(any(columns_flat))

    def test_predictor_two_deflate_matches_independent_decoders(self):
        """P1: Predictor=2 must be reversed, not interpreted as samples."""
        array = (np.arange(8 * 4, dtype=np.uint16).reshape(4, 8))
        pan = write_predictor_tiff(self.root / "predictor.tif", array)
        layout = batch_detector._read_strip_layout(pan, 8, 4)
        self.assertEqual(layout["predictor"], 2)
        # Windows are row-aligned even when one window is smaller than a strip.
        decoded = np.vstack(
            list(batch_detector._iter_windows(pan, layout, 8, 4, 2)))
        np.testing.assert_array_equal(decoded, array)
        with Image.open(pan) as image:
            np.testing.assert_array_equal(np.array(image), array)

        rows_flat, columns_flat, nodata = batch_detector._scan_flat_lines(
            pan, 8, 4, 2)
        self.assertIsNone(nodata)
        self.assertEqual(rows_flat, [False] * 4)
        self.assertFalse(
            any(columns_flat),
            "Predictor 残差被当成像素值，误判出平列")

    def test_unsupported_predictors_are_rejected(self):
        """P1: unsupported Predictor layouts fail loudly instead of misreading."""
        integer = write_predictor_tiff(
            self.root / "predictor-3.tif",
            np.arange(32, dtype=np.uint16).reshape(4, 8), predictor=3)
        with self.assertRaises(batch_detector.DetectionError) as caught:
            batch_detector._scan_flat_lines(integer, 8, 4, 2)
        self.assertIn("Predictor=3", str(caught.exception))

        floating = write_predictor_tiff(
            self.root / "float-predictor-2.tif",
            np.arange(32, dtype=np.float32).reshape(4, 8), predictor=2)
        with self.assertRaises(batch_detector.DetectionError) as caught:
            batch_detector._scan_flat_lines(floating, 8, 4, 2)
        self.assertIn("Predictor=2", str(caught.exception))

    def test_nan_nodata_diagnostics_stay_strict_json(self):
        """P2: a NaN NoData sentinel must not reach JSON as a raw float."""
        array = np.arange(32, dtype=np.float32).reshape(4, 8)
        array[2] = 5.0
        ifd = TiffImagePlugin.ImageFileDirectory_v2()
        ifd[42113] = "nan"
        pan = self.root / "nan.tif"
        Image.fromarray(array).save(pan, format="TIFF", tiffinfo=ifd)
        nosr = self._write_float_nosr()
        result = batch_detector.detect(
            self._ref(pan, 8, 4, "pan-key"),
            self._ref(nosr, 4, 2, "nosr-key"),
            {"mode": "real", "execution_mode": "demo"})
        scan = next(d for d in result["diagnostics"]
                    if d["code"] == "flatline_scan")
        self.assertEqual(scan["nodata"], "nan")
        self.assertEqual(scan["nodata_kind"], "nan")
        json.dumps(result, allow_nan=False)
        self.assertIn(("flat_row", (0, 2, 7, 2)),
                      {(c["label"], tuple(c["bbox"]))
                       for c in result["candidates"]})

    def _write_float_nosr(self):
        path = self.root / "NOSR-float.tif"
        Image.fromarray(
            np.full((2, 4), 1.5, dtype=np.float32)).save(path)
        return path

    def test_rejects_compression_outside_the_bounded_reader(self):
        _array, pan = self._pan()
        lzw = self.root / "lzw.tif"
        Image.open(pan).save(lzw, format="TIFF", compression="tiff_lzw")
        with self.assertRaises(batch_detector.DetectionError):
            batch_detector._scan_flat_lines(lzw, 64, 24, 8)

    def test_nodata_column_verdict_is_independent_of_window_rows(self):
        width, height = 8, 4
        array = np.zeros((height, width), dtype=np.uint16)
        array[:, 0] = np.array([7, 0, 7, 0], dtype=np.uint16)
        for row in range(height):
            array[row, 1:] = np.arange(1, width) + row * 10
        ifd = TiffImagePlugin.ImageFileDirectory_v2()
        ifd[42113] = "0"
        pan = self.root / "nodata.tif"
        Image.fromarray(array).save(pan, format="TIFF", tiffinfo=ifd)
        for rows_per_window in (1, 2, 4):
            _rows, columns, nodata = batch_detector._scan_flat_lines(
                pan, width, height, rows_per_window)
            self.assertEqual(nodata, 0.0)
            self.assertTrue(
                columns[0],
                f"window_rows={rows_per_window} 时第 0 列应跨窗口累计为平线")
            self.assertFalse(any(columns[1:]))


_PEAK_RSS_PROBE = r"""
import json
import sys

from backend.services import batch_detector


def peak_rss_bytes():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        counter = PMC()
        counter.cb = ctypes.sizeof(PMC)
        if not psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(), ctypes.byref(counter),
                counter.cb):
            raise SystemExit("GetProcessMemoryInfo failed")
        return int(counter.PeakWorkingSetSize)
    import resource

    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


path, width, height, rows = (
    sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
rows_flat, columns_flat, nodata = batch_detector._scan_flat_lines(
    path, width, height, rows)
print(json.dumps({"peak": peak_rss_bytes(), "rows": len(rows_flat)}))
"""


class RealDetectorMemoryTests(unittest.TestCase):
    """P1 1: peak memory must be bounded by the window, not the image area."""

    WIDTH = 8192
    WINDOW_ROWS = 32

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="d1-mem-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.repo_root = Path(__file__).resolve().parents[2]

    def _image(self, height):
        path = self.root / f"pan-{height}.tif"
        # 单条带整幅图：最坏布局，读取器仍必须按窗口流式解码。
        Image.new("L", (self.WIDTH, height), 0).save(path, format="TIFF")
        return path

    def _peak(self, path, height):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.repo_root) + os.pathsep + env.get(
            "PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-c", _PEAK_RSS_PROBE, str(path),
             str(self.WIDTH), str(height), str(self.WINDOW_ROWS)],
            cwd=self.repo_root, env=env, capture_output=True, text=True,
            timeout=180)
        self.assertEqual(
            proc.returncode, 0,
            f"峰值内存探针失败：{proc.stderr[-2000:]}")
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["rows"], height)
        return payload["peak"]

    def test_peak_rss_does_not_grow_with_image_height(self):
        small = self._image(4096)
        large = self._image(8192)
        small_peak = self._peak(small, 4096)
        large_peak = self._peak(large, 8192)
        growth = large_peak - small_peak
        self.assertLess(
            growth, 16 * 1024 * 1024,
            "固定窗口下峰值内存随影像高度增长 "
            f"{growth / 1048576:.1f} MiB："
            f"4096 行 {small_peak / 1048576:.1f} MiB → "
            f"8192 行 {large_peak / 1048576:.1f} MiB")
        self.assertLess(
            large_peak, 512 * 1024 * 1024,
            f"8192×{self.WIDTH} 单条带扫描峰值过高："
            f"{large_peak / 1048576:.1f} MiB")


class PolygonRasterTests(unittest.TestCase):
    """P1 3: clipping must not change the visible pixel area."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="d1-poly-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _array(self, path):
        with Image.open(path) as image:
            return np.asarray(image)

    def _expected(self, points, width, height):
        image = Image.new("L", (width, height), 0)
        ImageDraw.Draw(image).polygon([tuple(p) for p in points], fill=255)
        return np.asarray(image)

    def test_review_triangle_keeps_36_pixels(self):
        points = [[-5, 5], [5, -5], [5, 5]]
        out = self.root / "triangle.tif"
        meta = batch_mask.build_mask(
            out, width=10, height=10,
            candidates=[{"kind": "polygon", "points": points}],
            provenance="mock")
        expected = self._expected(points, 10, 10)
        self.assertEqual(int(np.count_nonzero(expected)), 36)
        self.assertEqual(meta["foreground_pixels"], 36)
        np.testing.assert_array_equal(self._array(out), expected)

    def test_cross_strip_diagonal_polygon_is_pixel_identical(self):
        points = [[-3, 12], [18, -2], [21, 9], [4, 21]]
        arrays = []
        for rows in (1, 2, 5, 7):
            out = self.root / f"diagonal-{rows}.tif"
            batch_mask.build_mask(
                out, width=22, height=22,
                candidates=[{"kind": "polygon", "points": points}],
                provenance="mock", rows_per_strip=rows)
            arrays.append(self._array(out))
        for other in arrays[1:]:
            np.testing.assert_array_equal(other, arrays[0])

    def test_corner_and_multi_edge_polygons_match_the_original_geometry(self):
        cases = {
            "corner": [[-6, -4], [3, -4], [3, 2], [-6, 2]],
            "multi_edge": [[-4, 10], [25, -3], [23, 18], [8, 15], [8, 5]],
        }
        for name, points in cases.items():
            out = self.root / f"{name}.tif"
            meta = batch_mask.build_mask(
                out, width=20, height=20,
                candidates=[{"kind": "polygon", "points": points}],
                provenance="mock", rows_per_strip=3)
            expected = self._expected(points, 20, 20)
            np.testing.assert_array_equal(self._array(out), expected)
            self.assertEqual(
                meta["foreground_pixels"], int(np.count_nonzero(expected)))


class MaskConfigTests(unittest.TestCase):
    """P2 6: env parsing must be int + range checked, not string passthrough."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="d2-config-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _build(self, name="config.tif", **kwargs):
        return batch_mask.build_mask(
            self.root / name, width=257, height=130,
            candidates=[{"bbox": [0, 0, 256, 129]}], provenance="mock",
            **kwargs)

    def test_rows_per_strip_env_is_parsed_as_an_integer(self):
        with mock.patch.dict(
                os.environ, {"SR_BATCH_MASK_ROWS_PER_STRIP": "64"}):
            meta = self._build()
        self.assertEqual(meta["strip_count"], math.ceil(130 / 64))

    def test_illegal_rows_per_strip_env_is_rejected(self):
        for raw in ("abc", "0", "-3", "70000"):
            with mock.patch.dict(
                    os.environ, {"SR_BATCH_MASK_ROWS_PER_STRIP": raw}):
                with self.assertRaises(batch_mask.MaskError, msg=raw):
                    self._build(name=f"bad-{raw}.tif")

    def test_overlay_edge_env_is_bounded(self):
        out = self.root / "overlay-ok.tif"
        with mock.patch.dict(
                os.environ, {"SR_BATCH_MASK_OVERLAY_MAX_EDGE": "32"}):
            meta = batch_mask.build_mask(
                out, width=37, height=23,
                candidates=[{"bbox": [0, 0, 36, 22]}], provenance="mock",
                overlay_path=self.root / "overlay.png")
        self.assertEqual(meta["overlay"]["scale"], 2)
        for raw in ("abc", "8", "9000"):
            with mock.patch.dict(
                    os.environ, {"SR_BATCH_MASK_OVERLAY_MAX_EDGE": raw}):
                with self.assertRaises(batch_mask.MaskError, msg=raw):
                    self._build(name=f"edge-{raw}.tif")


class DecodeCapTests(unittest.TestCase):
    """P2 7: malformed/oversized strips are rejected before large reads."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="d2-caps-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def _ifd(data):
        endian = "<" if data[:2] == b"II" else ">"
        offset = struct.unpack(endian + "I", data[4:8])[0]
        count = struct.unpack(endian + "H", data[offset:offset + 2])[0]
        entries = {}
        for index in range(count):
            pos = offset + 2 + index * 12
            tag, field_type, length = struct.unpack(
                endian + "HHI", data[pos:pos + 8])
            entries[tag] = (pos, field_type, length, data[pos + 8:pos + 12])
        return endian, entries

    def _patch_long(self, path, tag, value, index=0):
        data = bytearray(path.read_bytes())
        endian, entries = self._ifd(data)
        pos, field_type, length, raw = entries[tag]
        self.assertEqual(field_type, 4)
        if length == 1:
            data[pos + 8:pos + 12] = struct.pack(endian + "I", value)
        else:
            offset = struct.unpack(endian + "I", raw)[0]
            at = offset + 4 * index
            data[at:at + 4] = struct.pack(endian + "I", value)
        path.write_bytes(data)

    def _valid_mask(self, name="valid.tif", rows=4):
        path = self.root / name
        batch_mask.build_mask(
            path, width=16, height=12,
            candidates=[{"bbox": [0, 0, 15, 11]}],
            provenance="mock", rows_per_strip=rows)
        return path

    def test_decompression_never_exceeds_the_strip_budget(self):
        oversized = zlib.compress(b"\x00" * 101)
        with self.assertRaises(batch_mask.MaskError):
            batch_mask._decompress_strip(oversized, 100, 0)
        truncated = zlib.compress(b"\x00" * 101)[:-2]
        with self.assertRaises(batch_mask.MaskError):
            batch_mask._decompress_strip(truncated, 200, 0)

    def test_strip_byte_count_and_offset_outside_the_file_are_rejected(self):
        path = self._valid_mask("count.tif")
        self._patch_long(path, 279, 0x7FFFFFFF)
        with self.assertRaises(batch_mask.MaskError):
            batch_mask.verify_mask(path, width=16, height=12)

        path = self._valid_mask("offset.tif")
        self._patch_long(path, 273, 0x7FFFFFFF)
        with self.assertRaises(batch_mask.MaskError):
            batch_mask.verify_mask(path, width=16, height=12)

    def test_strip_array_count_is_bounded_before_reading(self):
        path = self._valid_mask("array.tif")
        data = bytearray(path.read_bytes())
        endian, entries = self._ifd(data)
        pos, _field_type, _length, _raw = entries[273]
        data[pos + 4:pos + 8] = struct.pack(endian + "I", 1_000_000)
        path.write_bytes(data)
        with self.assertRaises(batch_mask.MaskError):
            batch_mask.verify_mask(path, width=16, height=12)

    def test_pillow_fallback_refuses_a_whole_image_decode(self):
        with self.assertRaises(batch_mask.MaskError):
            batch_mask._check_with_pillow(
                self.root / "missing.tif",
                batch_mask.MAX_PILLOW_VERIFY_PIXELS, 2)


class BundleRecoveryTests(unittest.TestCase):
    """P2 5: published-but-uncommitted bundles are validated, not orphaned."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="d2-recovery-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.pan = target()
        self.path = self.root / "artifacts" / "item-1" / "detect-1" / "mask.tif"

    def _detection(self, bbox=(1, 1, 20, 15)):
        detection = batch_detector.detect(self.pan)
        return {
            **detection,
            "candidates": [{
                "candidate_id": "c1",
                "kind": "bbox",
                "bbox": list(bbox),
                "points": None,
                "score": None,
                "label": "center",
            }],
        }

    def test_valid_published_bundle_is_reused_with_the_same_digest(self):
        first = batch_mask.build_mask_result(
            self._detection(), self.pan, {"path": self.path})
        second = batch_mask.build_mask_result(
            self._detection(), self.pan, {"path": self.path})
        self.assertTrue(second["reused"])
        self.assertEqual(second["sha256"], first["sha256"])
        self.assertEqual(second["overlay"]["sha256"],
                         first["overlay"]["sha256"])

    def test_candidate_digest_change_forces_a_rebuild(self):
        batch_mask.build_mask_result(
            self._detection(), self.pan, {"path": self.path})
        rebuilt = batch_mask.build_mask_result(
            self._detection((2, 2, 21, 16)), self.pan, {"path": self.path})
        self.assertFalse(rebuilt["reused"])
        diagnostics = {d["code"]: d for d in rebuilt["diagnostics"]}
        self.assertEqual(
            diagnostics["mask_bundle_rebuilt"]["reason"], "candidate_mismatch")

    def test_tampered_mask_digest_forces_a_rebuild(self):
        batch_mask.build_mask_result(
            self._detection(), self.pan, {"path": self.path})
        self.path.write_bytes(self.path.read_bytes() + b"\x00")
        rebuilt = batch_mask.build_mask_result(
            self._detection(), self.pan, {"path": self.path})
        self.assertFalse(rebuilt["reused"])
        diagnostics = {d["code"]: d for d in rebuilt["diagnostics"]}
        self.assertEqual(
            diagnostics["mask_bundle_rebuilt"]["reason"], "mask_digest_mismatch")
        self.assertTrue(self.path.is_file())
        batch_mask.verify_mask(
            self.path, width=37, height=23, sha256=rebuilt["sha256"])

    def test_overlapping_builder_cannot_delete_an_active_temp_dir(self):
        """P2 7: the round lock protects the first builder's live temp dir."""
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        outcome = {}
        original = batch_mask.build_mask

        def slow(path, **kwargs):
            entered.set()
            release.wait(10)
            return original(path, **kwargs)

        def build():
            try:
                outcome["result"] = batch_mask.build_mask_result(
                    self._detection(), self.pan, {"path": self.path})
            except BaseException as exc:  # noqa: BLE001 — surfaced below
                outcome["error"] = exc

        with mock.patch.object(batch_mask, "build_mask", side_effect=slow):
            thread = threading.Thread(target=build)
            thread.start()
            self.assertTrue(entered.wait(10), "第一个构建者未进入写入阶段")
            parent = self.path.parent.parent
            active = [
                child for child in parent.iterdir()
                if child.is_dir()
                and child.name.startswith(self.path.parent.name + ".tmp-")
            ]
            self.assertTrue(active, "第一个构建者缺少活动临时目录")
            with self.assertRaises(batch_mask.MaskError) as caught:
                batch_mask.build_mask_result(
                    self._detection(), self.pan, {"path": self.path})
            self.assertIn("另一", str(caught.exception))
            self.assertTrue(
                active[0].is_dir(),
                "第二个构建者删除了另一个进程的活动临时目录")
            release.set()
            thread.join(10)
        self.assertNotIn("error", outcome, outcome.get("error"))
        self.assertTrue(self.path.is_file())
        self.assertTrue(outcome["result"]["path"].endswith("mask.tif"))

    def test_cross_process_round_lock_is_retryable(self):
        """P2 7: a different process holding the round blocks without cleanup."""
        repo_root = Path(__file__).resolve().parents[2]
        script = (
            "import sys, time\n"
            "from pathlib import Path\n"
            "from backend.services import batch_mask\n"
            "with batch_mask._round_lock(Path(sys.argv[1])):\n"
            "    print('locked', flush=True)\n"
            "    time.sleep(60)\n")
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get(
            "PYTHONPATH", "")
        proc = subprocess.Popen(
            [sys.executable, "-c", script, str(self.path.parent)],
            cwd=repo_root, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            line = proc.stdout.readline().strip()
            self.assertEqual(
                line, "locked",
                "子进程未拿到轮次锁：" + (proc.stderr.read()[-2000:]
                                     if not line else ""))
            with self.assertRaises(batch_mask.MaskError) as caught:
                batch_mask.build_mask_result(
                    self._detection(), self.pan, {"path": self.path})
            self.assertIn("另一进程", str(caught.exception))
        finally:
            proc.kill()
            proc.wait(10)
        # The OS releases a terminated process's byte-range lock asynchronously;
        # the worker's retry/backoff covers that window here too.
        deadline = time.time() + 10
        while True:
            try:
                result = batch_mask.build_mask_result(
                    self._detection(), self.pan, {"path": self.path})
                break
            except batch_mask.MaskError:
                if time.time() >= deadline:
                    raise
                time.sleep(0.05)
        self.assertTrue(result["path"].endswith("mask.tif"))
        self.assertTrue(self.path.is_file())

    def test_incomplete_bundle_and_stale_temps_are_cleaned(self):
        final_dir = self.path.parent
        final_dir.mkdir(parents=True)
        (final_dir / "overlay.png").write_bytes(b"not a real png")
        stale = final_dir.with_name(final_dir.name + ".tmp-999-1")
        stale.mkdir()
        (stale / "half.tif").write_bytes(b"x")

        result = batch_mask.build_mask_result(
            self._detection(), self.pan, {"path": self.path})
        self.assertFalse(result["reused"])
        diagnostics = {d["code"]: d for d in result["diagnostics"]}
        self.assertEqual(
            diagnostics["mask_bundle_rebuilt"]["reason"], "bundle_incomplete")
        self.assertFalse(stale.exists())
        self.assertTrue(self.path.is_file())
        self.assertTrue((final_dir / "overlay.png").is_file())


class OverlayApiTests(unittest.TestCase):
    """P1 2: path-guarded Overlay reads bound to Goal/Item/source identity."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="d3-api-")
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {
            "SR_BATCH_ARTIFACT_DIR": str(self.root / "artifacts"),
            "SR_AGENT_DB": str(self.root / "db"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        from backend.services.store import Store
        self.store = Store(self.root / "db")
        self.addCleanup(self.store.close)

        pan = target()
        detection = batch_detector.detect(pan)
        self.mask_path = (self.root / "artifacts" / "work" / "goal-1"
                          / "item-1" / "detect-1" / "mask.tif")
        self.mask = batch_mask.build_mask_result(
            detection, pan, {"path": self.mask_path})
        self.goal = self._make_goal(self.mask)
        self.overlay_path = self.mask_path.parent / "overlay.png"

    def _make_goal(self, mask, request_key="overlay-api"):
        checkpoint = {"workflow": "batch", "mode": "demo", "manifest_ref": "m"}
        item = {
            "item_id": "item-1",
            "ordinal": 1,
            "name": "scene-1",
            "status": "PENDING",
            "resource_key": "pan-key",
            "checkpoint": {"mask": mask, "target_image": target()},
        }
        return self.store.create_batch_goal(
            title="overlay", request_key=request_key,
            content_sha256="content", checkpoint=checkpoint, items=[item],
            max_model_calls=0)

    def _client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from backend.services import goal_runtime
        app = FastAPI()
        app.state.store = self.store
        app.include_router(goal_runtime.router)
        return TestClient(app)

    def _patch_checkpoint(self, checkpoint):
        db = self.store._db()
        db.execute(
            "UPDATE agent_goal_steps SET checkpoint=? "
            "WHERE goal_id=? AND item_id=?",
            (json.dumps(checkpoint, ensure_ascii=False),
             self.goal["goal_id"], "item-1"))
        db.commit()

    def test_overlay_metadata_is_bound_to_the_item_and_source(self):
        with self._client() as client:
            response = client.get(
                f"/api/goals/{self.goal['goal_id']}/items/item-1/overlay")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertEqual(body["contract_version"], "overlay.v1")
            self.assertEqual(body["goal_id"], self.goal["goal_id"])
            self.assertEqual(body["item_id"], "item-1")
            self.assertEqual(body["source"]["image_key"], "pan-key")
            self.assertEqual(body["source"]["revision"], "rev-1")
            self.assertEqual(body["provenance"], "mock")
            self.assertEqual(body["mask"]["sha256"], self.mask["sha256"])
            self.assertEqual(
                body["overlay"]["sha256"], self.mask["overlay"]["sha256"])
            self.assertTrue(
                body["overlay"]["url"].endswith("/items/item-1/overlay.png"))
            self.assertTrue(body["candidates"])

            png = client.get(
                f"/api/goals/{self.goal['goal_id']}/items/item-1/overlay.png")
            self.assertEqual(png.status_code, 200)
            self.assertEqual(png.headers["content-type"], "image/png")
            self.assertEqual(png.content, self.overlay_path.read_bytes())

    def test_nan_nodata_detection_serves_overlay_json(self):
        """P2: a real NaN NoData image must not break the Overlay response."""
        pan_array = np.arange(32, dtype=np.float32).reshape(4, 8)
        pan_array[2] = 5.0
        ifd = TiffImagePlugin.ImageFileDirectory_v2()
        ifd[42113] = "nan"
        pan_path = self.root / "nan-PAN.tif"
        Image.fromarray(pan_array).save(pan_path, format="TIFF", tiffinfo=ifd)
        nosr_path = self.root / "nan-NOSR.tif"
        Image.fromarray(
            np.full((2, 4), 1.5, dtype=np.float32)).save(nosr_path)

        def ref(path, width, height, key):
            return {
                "item_id": "item-1", "path": path.as_posix(),
                "image_key": key, "width": width, "height": height,
                "revision": preview_jpg.source_revision(path),
            }

        pan_ref = ref(pan_path, 8, 4, "pan-key")
        detection = batch_detector.detect(
            pan_ref, ref(nosr_path, 4, 2, "nosr-key"),
            {"mode": "real", "execution_mode": "demo"})
        mask_path = (self.root / "artifacts" / "work" / "goal-nan"
                     / "item-1" / "detect-1" / "mask.tif")
        mask = batch_mask.build_mask_result(
            detection, pan_ref, {"path": mask_path})
        doc = json.loads(
            (mask_path.parent / "overlay.json").read_text(encoding="utf-8"))
        json.dumps(doc, allow_nan=False)
        goal = self._make_goal(mask, request_key="overlay-nan")
        with self._client() as client:
            response = client.get(
                f"/api/goals/{goal['goal_id']}/items/item-1/overlay")
            self.assertEqual(response.status_code, 200)
            body = response.json()
            scan = next(d for d in body["diagnostics"]
                        if d["code"] == "flatline_scan")
            self.assertEqual(scan["nodata"], "nan")
            self.assertEqual(scan["nodata_kind"], "nan")
            self.assertNotIn("NaN", response.text)

    def test_overlay_paths_outside_the_artifact_root_are_refused(self):
        outside = self.root / "outside.png"
        outside.write_bytes(self.overlay_path.read_bytes())
        checkpoint = {
            "mask": {**self.mask, "overlay": {
                **self.mask["overlay"], "path": outside.as_posix()}},
        }
        self._patch_checkpoint(checkpoint)
        with self._client() as client:
            response = client.get(
                f"/api/goals/{self.goal['goal_id']}/items/item-1/overlay")
            self.assertEqual(response.status_code, 403)

    def test_overlay_digest_drift_is_refused(self):
        self.overlay_path.write_bytes(self.overlay_path.read_bytes() + b"x")
        with self._client() as client:
            response = client.get(
                f"/api/goals/{self.goal['goal_id']}/items/item-1/overlay")
            self.assertEqual(response.status_code, 409)

    def test_overlay_manifest_for_another_item_is_refused(self):
        doc_path = self.mask_path.parent / "overlay.json"
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        doc["item_id"] = "item-2"
        doc_path.write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        with self._client() as client:
            response = client.get(
                f"/api/goals/{self.goal['goal_id']}/items/item-1/overlay")
            self.assertEqual(response.status_code, 409)

    def test_item_without_a_published_overlay_is_404(self):
        self._patch_checkpoint({"mask": {"path": self.mask_path.as_posix()}})
        with self._client() as client:
            response = client.get(
                f"/api/goals/{self.goal['goal_id']}/items/item-1/overlay")
            self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
