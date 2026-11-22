from PIL import Image
import numpy as np

from backend.services.repair_effect import assess_repair_effect


def test_effect_measures_only_the_submitted_mask(tmp_path):
    before = np.zeros((16, 16), dtype=np.uint16)
    after = before.copy()
    after[:, 8:] = 80
    mask = np.ones((16, 16), dtype=np.uint8) * 255

    def write(name, data):
        path = tmp_path / (name + ".tif")
        Image.fromarray(data).save(path)
        return str(path)

    binding = {
        "job_id": "17",
        "backup": {"path": write("before", before)},
        "output": {"path": write("after", after)},
        "mask": {"path": write("mask", mask), "sha256": "fixture"},
        "regions": [{"candidate_id": "r1", "bbox": [0, 0, 15, 15]}],
    }
    report = assess_repair_effect("scene", "17", binding)
    assert report["regions"][0]["gradient_p98"]["delta"] == 80
    mask[:, 8:] = 0
    binding["mask"]["path"] = write("partial-mask", mask)
    report = assess_repair_effect("scene", "17", binding)
    assert report["regions"][0]["gradient_p98"]["delta"] == 0
    assert report["regions"][0]["metrics"]["mask_boundary_jump_dn"]["delta"] == 80
    binding["output"]["path"] = binding["backup"]["path"]
    unchanged = assess_repair_effect("scene", "17", binding)
    assert unchanged["regions"][0]["conclusion"] == "unchanged"


def test_paired_edge_width_halo_and_banding_direction():
    from backend.services.repair_effect import _measure

    old = np.tile(np.interp(np.arange(40), [0, 17, 22, 39], [50, 50, 150, 150]), (32, 1))
    new = np.tile(np.r_[np.full(19, 50), 43, 157, np.full(19, 150)], (32, 1))
    valid = np.ones(old.shape, dtype=bool)
    result = _measure(old, new, valid, valid, valid)
    assert result["metrics"]["edge_width_px"]["delta"] < 0
    assert result["metrics"]["edge_halo_fraction"]["delta"] > 0
    reverse = _measure(new, old, valid, valid, valid)
    assert reverse["metrics"]["edge_width_px"]["delta"] > 0
    striped = old + np.arange(32)[:, None] % 2 * 4
    result = _measure(striped, old, valid, valid, valid)
    assert result["metrics"]["row_banding_dn"]["delta"] < 0


def test_shift_in_stable_context_blocks_comparison():
    from backend.services.repair_effect import _measure

    old = np.random.default_rng(20).uniform(20, 200, (32, 32))
    new = np.roll(old, 1, axis=1)
    valid = np.ones(old.shape, dtype=bool)
    mask = np.zeros(old.shape, dtype=bool)
    mask[10:22, 10:22] = True
    result = _measure(old, new, valid, mask, mask)
    assert result["evidence_gap"] == "registration_shift"
    assert result["shift_yx"] == [0, 1]

