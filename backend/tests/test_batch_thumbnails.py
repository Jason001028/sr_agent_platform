from pathlib import Path
from unittest.mock import patch
from backend.api.app import _thumbnail_url
from backend.tests.test_api import SceneListMixin, make_scene
from backend.api import paths

class TestBatchThumbnails(SceneListMixin):
    def test_scene_shared_thumbnail_for_pan_ori_nosr(self):
        root = Path(self._root.name)
        source = make_scene(root, "PRODUCT.tif")
        thumb = source.with_name("PRODUCT_thumb.jpg")
        self.assertIsNone(_thumbnail_url(source, root))
        thumb.write_bytes(b"existing")
        self.assertEqual(_thumbnail_url(source, root), "/disk-array/PRODUCT/PRODUCT_thumb.jpg")
        for name in ("PAN.tif", "PRODUCT_ori.tif", "PAN_ori.tif", "PRODUCT_SR_NOSR.tif"):
            self.assertEqual(_thumbnail_url(source.with_name(name), root),
                             "/disk-array/PRODUCT/PRODUCT_thumb.jpg")
        self.assertIsNone(_thumbnail_url(source.with_name("PRODUCT_SR.tif"), root))
        other = make_scene(root / "another", "PRODUCT.tif")
        self.assertIsNone(_thumbnail_url(other, root))
        other.with_name("PRODUCT_thumb.jpg").write_bytes(b"other")
        self.assertEqual(_thumbnail_url(other, root), "/disk-array/another/PRODUCT/PRODUCT_thumb.jpg")
        self.assertIsNone(_thumbnail_url(source, root / "other"))
        self.assertIsNone(_thumbnail_url(source, None))

    def test_ready_only_preserves_generation_without_jpeg_body(self):
        root = Path(self._root.name)
        source = make_scene(root, "PRODUCT.tif")
        with self.client(str(root)) as client:
            sid = paths.scene_id("PRODUCT/PRODUCT.tif")
            response = client.get(f"/api/scenes/{sid}/preview?div=2&ready_only=true")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"ready": True})
            self.assertTrue(source.with_name("PRODUCT_preview.jpg").is_file())
            image = client.get(f"/api/scenes/{sid}/preview?div=2")
            self.assertEqual(image.headers["content-type"], "image/jpeg")
            self.assertGreater(len(image.content), len(response.content))
