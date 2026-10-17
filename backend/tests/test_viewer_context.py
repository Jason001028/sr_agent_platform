import unittest

from backend.contracts.viewer_context import normalize_viewer_context, validate_viewer_context


def valid():
    return {
        "schema_version": 1,
        "scene": {"id": "scene-1", "name": "PAN.tif"},
        "source": {"kind": "scene"},
        "image": {"name": "PAN.tif", "width": 100, "height": 80,
                  "display_width": 50, "display_height": 40, "stretch": "linear"},
        "roi": None,
        "coordinates": {"image": "pixel", "display": "pixel", "origin": "top_left"},
        "capabilities": {"read": True, "write_original": False},
    }


class TestViewerContextContract(unittest.TestCase):
    def test_valid_snapshot_is_accepted(self):
        value = valid()
        self.assertEqual(validate_viewer_context(value), (True, None))
        self.assertEqual(normalize_viewer_context(value)["schema_version"], 1)

    def test_schema_version_is_required(self):
        value = valid()
        value["schema_version"] = 2
        ok, error = validate_viewer_context(value)
        self.assertFalse(ok)
        self.assertIn("unsupported schema_version", error)

    def test_image_dimensions_must_be_positive_integers(self):
        value = valid()
        value["image"]["width"] = 0
        ok, error = validate_viewer_context(value)
        self.assertFalse(ok)
        self.assertIn("image.width", error)

    def test_roi_is_nullable_but_must_be_an_object(self):
        value = valid()
        value["roi"] = []
        ok, error = validate_viewer_context(value)
        self.assertFalse(ok)
        self.assertIn("context.roi", error)


if __name__ == "__main__":
    unittest.main()
