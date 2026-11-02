import base64
import os
from unittest import mock

from backend.tests.test_preview_scope import PreviewScopeBase, make_tif


def fingerprint(path):
    data = path.read_bytes()
    offsets = [0, max(0, (len(data) - 4096) // 2), max(0, len(data) - 4096)]
    return {"name": path.name, "size": len(data), "samples": [
        base64.b64encode(data[offset:offset + 4096]).decode("ascii") for offset in offsets]}


class TestSourceMatch(PreviewScopeBase):
    def test_automatic_candidate_also_requires_matching_content(self):
        scene = self.array / "scan_20260101"
        path = make_tif(scene / "scan_20260101.tif")
        (scene / "scan_20260101_meta.xml").write_text("<meta/>")
        original = fingerprint(path)
        body = {"name": path.name, "size_bytes": path.stat().st_size,
                "source_fingerprint": original}
        with mock.patch.dict(os.environ, {"SR_SCENE_PATH_TEMPLATE": scene.as_posix()}):
            client = self.client()
            response = client.post("/api/scenes/resolve", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            make_tif(path, flip=True)
            response = client.post("/api/scenes/resolve", json=body)
            self.assertEqual(response.status_code, 409, response.text)

    def test_explicit_external_file_matches_without_write_permission(self):
        path = make_tif(self.ext / "source.tif")
        response = self.client().post("/api/scenes/resolve", json={
            "path": str(path), "source_fingerprint": fingerprint(path)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["resolved"]["preview_only"])
        self.assertFalse(response.json()["resolved"]["sr_capable"])

    def test_same_name_and_size_with_different_content_rejected(self):
        path = make_tif(self.ext / "source.tif")
        original = fingerprint(path)
        make_tif(path, flip=True)
        self.assertEqual(path.stat().st_size, original["size"])
        response = self.client().post("/api/scenes/resolve", json={
            "path": str(path), "source_fingerprint": original})
        self.assertEqual(response.status_code, 409, response.text)

    def test_malformed_and_wrong_name_fingerprints_rejected(self):
        path = make_tif(self.ext / "source.tif")
        client = self.client()
        for fp in (None, {}, {**fingerprint(path), "name": "other.tif"},
                   {**fingerprint(path), "samples": ["!", "!", "!"]}):
            response = client.post("/api/scenes/resolve", json={
                "path": str(path), "source_fingerprint": fp})
            self.assertEqual(response.status_code, 409, response.text)

    def test_matching_content_cannot_bypass_preview_roots(self):
        path = make_tif(self.outside / "source.tif")
        response = self.client().post("/api/scenes/resolve", json={
            "path": str(path), "source_fingerprint": fingerprint(path)})
        self.assertEqual(response.status_code, 403, response.text)

    def test_directory_is_not_a_file_binding(self):
        path = make_tif(self.ext / "source.tif")
        response = self.client().post("/api/scenes/resolve", json={
            "path": str(self.ext), "source_fingerprint": fingerprint(path)})
        self.assertEqual(response.status_code, 400, response.text)

    def test_external_nosr_tag_preserves_readonly_capability(self):
        scene = self.ext / "scene"
        source = make_tif(scene / "PAN.tif")
        (scene / "scene_meta.xml").write_text("<meta/>")
        nosr = make_tif(scene / "PAN_NOSR.tif")
        response = self.client().post("/api/scenes/resolve", json={
            "path": str(nosr), "source_fingerprint": fingerprint(nosr)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["resolved"]["kind"], "nosr")
        self.assertFalse(response.json()["resolved"]["sr_capable"])
