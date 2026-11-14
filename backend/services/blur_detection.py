"""Read-only, experimental texture-loss evidence on an aligned analysis grid.

Callers must establish registration and explicitly pass NOSR as reference and
PAN as target. Scores are relative evidence, never calibrated probabilities.
No production thresholds or code are imported from the internal reference.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.ndimage import uniform_filter, binary_dilation, binary_erosion

VERSION = "smart-compare-0.3-experimental"


@dataclass(frozen=True)
class BlurConfig:
    window: int = 15
    calibration_scale: float | None = None
    sensor: str = "generic"
    frequency_review: bool = False
    scene_suppression: bool = True
    log_ratio_baseline: float | None = None

    def __post_init__(self):
        if isinstance(self.window, bool) or not isinstance(self.window, int) or not 3 <= self.window <= 63 or self.window % 2 == 0:
            raise ValueError("Texture window must be an odd integer in [3, 63]")
        if self.calibration_scale is not None and (isinstance(self.calibration_scale, bool)
                or not isinstance(self.calibration_scale, (int, float))
                or not np.isfinite(self.calibration_scale) or self.calibration_scale <= 0):
            raise ValueError("Calibration scale must be finite and positive")
        if self.log_ratio_baseline is not None and (isinstance(self.log_ratio_baseline,bool)
                or not isinstance(self.log_ratio_baseline,(int,float)) or not np.isfinite(self.log_ratio_baseline)
                or abs(self.log_ratio_baseline)>100 or self.calibration_scale is None):
            raise ValueError("Baseline requires a fixed scale and a finite log ratio in [-100,100]")


def local_statistics(image, valid, window):
    """Masked population statistics; missing pixels do not become zero DN."""
    # Centre before squaring: E[x²]-E[x]² loses small texture at large DN.
    anchor = float(np.asarray(image)[valid].mean()) if valid.any() else 0.
    x = np.where(valid, np.asarray(image, dtype=np.float64) - anchor, 0)
    weight = uniform_filter(valid.astype(np.float64), window, mode="reflect")
    denominator = np.maximum(weight, np.finfo(np.float64).eps)
    mean = uniform_filter(x, window, mode="reflect") / denominator
    variance = np.maximum(uniform_filter(x * x, window, mode="reflect") / denominator - mean * mean, 0)
    return mean + anchor, variance, weight


def analyze_texture(reference, target, *, valid=None, config=None):
    """Compute two-scale variance/roughness evidence without reading source files.

    Window units are analysis pixels. Returned halo covers the largest window
    and the one-pixel roughness stencil; tiled callers must crop this halo.
    """
    config = config or BlurConfig()
    a, b = np.asarray(reference, dtype=np.float64), np.asarray(target, dtype=np.float64)
    if a.ndim != 2 or a.shape != b.shape or min(a.shape) < 3:
        raise ValueError("Expected aligned 2D arrays with matching dimensions >= 3")
    finite = np.isfinite(a) & np.isfinite(b)
    if valid is not None:
        if np.shape(valid) != a.shape:
            raise ValueError("Validity grid does not match images")
        finite &= np.asarray(valid, dtype=bool)
    if not finite.any():
        raise ValueError("No jointly valid pixels")
    _, va, support = local_statistics(a, finite, config.window)
    _, vb, _ = local_statistics(b, finite, config.window)
    positive = va[finite & (va > 0)]
    floor = max(float(np.median(positive)) * 1e-6 if positive.size else 0, 1e-12)
    ratio = va / np.maximum(vb, floor)
    # A global gain change shifts all log ratios: remove that scene baseline.
    log_ratio = np.log(np.maximum(ratio, 1e-12))
    informative = finite & (va > floor * 100) & (support >= .8)
    baseline = (float(np.median(log_ratio[informative])) if informative.any() else 0.) if config.calibration_scale is None else 0.
    if config.log_ratio_baseline is not None: baseline = config.log_ratio_baseline
    loss = np.maximum(log_ratio - max(0., baseline), 0.)
    large = min(127, config.window * 2 + 1)
    _, ca, _ = local_statistics(a, finite, large)
    _, cb, _ = local_statistics(b, finite, large)
    # Fine/coarse texture ratio rejects uniform contrast changes.
    texture = np.maximum(np.log(np.maximum(va / np.maximum(ca, floor), 1e-12)) -
                         np.log(np.maximum(vb / np.maximum(cb, floor), 1e-12)), 0.)
    # Adjacent-pixel energy divided by local variance measures roughness rather
    # than gain. Both endpoints must be valid (no gradients across NoData).
    def roughness(x):
        energy = np.zeros_like(x)
        counts = np.zeros_like(x)
        clean = np.where(finite, x, 0)
        for axis in (0, 1):
            slices = (slice(1, None), slice(None)) if axis == 0 else (slice(None), slice(1, None))
            previous = (slice(None, -1), slice(None)) if axis == 0 else (slice(None), slice(None, -1))
            pairs = finite[slices] & finite[previous]
            energy[slices] += np.where(pairs, np.diff(clean, axis=axis) ** 2, 0)
            counts[slices] += pairs
        return uniform_filter(energy, config.window, mode="reflect") / np.maximum(
            uniform_filter(counts, config.window, mode="reflect"), 1e-12)
    rough_a = roughness(a) / np.maximum(va, floor)
    rough_b = roughness(b) / np.maximum(vb, floor)
    texture = np.maximum(texture, np.maximum(np.log(np.maximum(rough_a, 1e-12) / np.maximum(rough_b, 1e-12)), 0))
    raw = np.sqrt(loss * texture)
    population = raw[informative]
    scale = config.calibration_scale or (max(float(np.quantile(population, .99)), .25) if population.size else 1.)
    score = np.where(informative, np.clip(raw / scale, 0, 1), 0).astype(np.float32)
    return {
        "score": score, "varianceRatio": np.where(finite, ratio, 0).astype(np.float32),
        "textureLoss": texture.astype(np.float32), "validity": finite, "informative": informative,
        "referenceVariance": va, "targetVariance": vb,
        "algorithmVersion": VERSION, "scoreSemantics": "relative-texture-loss-not-probability",
        "normalization": {"scale": scale, "logRatioBaseline": baseline,
                          "mode": "fixed" if config.calibration_scale else "scene-q99"},
        "windowsAnalysisPixels": [config.window, large], "halo": large // 2 + 1,
        "maskAvailability": {name: "unverified" for name in ("cloud", "water", "shadow")},
        "calibrationStatus": "experimental-unvalidated",
    }


def config_from_dict(raw=None):
    raw = {} if raw is None else raw
    if not isinstance(raw, dict):
        raise ValueError("smartOptions must be an object")
    allowed = {"window", "calibrationScale", "logRatioBaseline", "sensor", "frequencyReview", "sceneSuppression"}
    if set(raw) - allowed:
        raise ValueError("Unknown LTD option")
    for key in ("frequencyReview", "sceneSuppression"):
        if key in raw and not isinstance(raw[key], bool):
            raise ValueError(f"{key} must be boolean")
    sensor = raw.get("sensor", "generic")
    if not isinstance(sensor, str) or len(sensor) > 64:
        raise ValueError("Invalid sensor")
    return BlurConfig(raw.get("window", 15), raw.get("calibrationScale"), sensor,
                      raw.get("frequencyReview", False), raw.get("sceneSuppression", True),raw.get("logRatioBaseline"))


def apply_scene_masks(evidence, masks, config):
    """Trusted caller supplies already registered masks; never infer missing masks."""
    availability = evidence["maskAvailability"]
    overlaps = {}
    for name in availability:
        spec = (masks or {}).get(name)
        if spec is None:
            continue
        if not isinstance(spec, dict) or spec.get("registered") is not True or not spec.get("sourceIdentity"):
            availability[name] = spec.get("status", "unverified-registration") if isinstance(spec, dict) else "unverified-registration"
            continue
        mask = np.asarray(spec.get("mask"))
        if mask.shape != evidence["score"].shape or mask.dtype != np.bool_:
            availability[name] = "unverified-grid"
            continue
        availability[name] = "available" if config.scene_suppression else "available-disabled"
        overlaps[name] = mask
        # Scene interference affects confidence, never removes the underlying
        # texture-loss candidate. Multiple masks must not become a hard veto.
    evidence["sceneMasks"] = overlaps


def frequency_evidence(reference, target, valid):
    """Candidate-only, bounded radial high-frequency energy; never a veto."""
    # A native crop preserves frequency units. Strided decimation aliases fine
    # texture and can even sample only NoData despite 80% full-patch validity.
    y, x = (max(0, (n - 256) // 2) for n in reference.shape)
    crop = np.s_[y:y+256, x:x+256]
    reference, target, valid = reference[crop], target[crop], valid[crop]
    valid = valid & np.isfinite(reference) & np.isfinite(target)
    if min(reference.shape) < 8 or valid.mean() < .8:
        return {"status": "insufficient-data", "experimental": True}
    # Limit FFT working set; this diagnostic is not the detector itself.
    energies = []
    for x in (reference, target):
        x = x.astype(float)
        x = np.where(valid, x, np.mean(x[valid]))
        x -= x.mean()
        window = np.outer(np.hanning(x.shape[0]), np.hanning(x.shape[1]))
        power = abs(np.fft.rfft2(x * window)) ** 2
        fy = np.fft.fftfreq(x.shape[0])[:, None]
        fx = np.fft.rfftfreq(x.shape[1])[None, :]
        high = np.hypot(fx, fy) >= .25
        energies.append(float(power[high].sum() / max(power.sum(), 1e-12)))
    return {"status": "reviewed", "experimental": True, "referenceHighFrequency": energies[0],
            "targetHighFrequency": energies[1], "sampleStride": 1,
            "cropOffset": [int(crop[1].start), int(crop[0].start)],
            "cropShape": list(reference.shape), "frequencyUnits": "cycles-per-analysis-pixel",
            "interpretation": "supporting-evidence-not-a-veto"}


def region_diagnostics(evidence, labels, region, reference, target, config):
    x, y, w, h = region["bbox"]
    slices = np.s_[y:y+h, x:x+w]
    # Label at the rounded centroid need not lie inside a concave region.
    local_labels = labels[slices]
    ids, counts = np.unique(local_labels[local_labels > 0], return_counts=True)
    label_id = region.pop("_label", int(ids[np.argmax(counts)]) if ids.size else 0)
    inside = local_labels == label_id
    values = evidence["score"][slices][inside]
    ratio = evidence["varianceRatio"][slices][inside]
    texture = evidence["textureLoss"][slices][inside]
    yy, xx = np.nonzero(inside)
    orientation = 0.
    if len(xx) > 2:
        cov = np.cov(np.array([xx, yy]))
        _, vectors = np.linalg.eigh(cov)
        orientation = float(np.degrees(np.arctan2(vectors[1, -1], vectors[0, -1])))
    perimeter = max(1, int(np.count_nonzero(inside & ~binary_erosion(inside))))
    ey0,ex0=max(0,y-3),max(0,x-3)
    ey1,ex1=min(labels.shape[0],y+h+3),min(labels.shape[1],x+w+3)
    expanded=np.s_[ey0:ey1,ex0:ex1]
    expanded_inside=labels[expanded]==label_id
    ring = binary_dilation(expanded_inside, iterations=3) & (labels[expanded] == 0) & evidence["informative"][expanded]
    overlap = {name: float(mask[slices][inside].mean()) for name, mask in evidence.get("sceneMasks", {}).items()}
    diff = abs(reference[slices] - target[slices])
    valid_values = reference[slices][evidence["validity"][slices]]
    span = max(float(np.quantile(valid_values, .95)-np.quantile(valid_values, .05)), 1e-12)
    difference = float(diff[inside].mean()) / span
    cls = "joint_confirmed" if difference > .03 else "blur_candidate"
    if not values.size or float(texture.mean()) < .05:
        cls = "difference_only"
    frequency = frequency_evidence(reference[slices], target[slices], evidence["validity"][slices]) if config.frequency_review else {"status": "disabled", "experimental": True}
    return {"classification": cls, "reviewStatus": "pending-human-review",
            "scoreMean": float(values.mean()) if values.size else 0., "scoreMax": float(values.max()) if values.size else 0.,
            "scoreP95": float(np.quantile(values, .95)) if values.size else 0.,
            "varianceRatioMean": float(ratio.mean()) if ratio.size else 0.,
            "textureLossMean": float(texture.mean()) if texture.size else 0.,
            "surroundingTextureLoss": float(evidence["textureLoss"][expanded][ring].mean()) if ring.any() else None,
            "compactness": min(1., float(4 * np.pi * len(xx) / perimeter ** 2)),
            "aspectRatio": max(w, h) / max(1, min(w, h)), "orientationDegrees": orientation,
            "shape": "stripe" if max(w, h) / max(1, min(w, h)) > 8 else "continuous-region",
            "sceneOverlap": overlap, "confidence": "low" if config.scene_suppression and any(v > .5 for v in overlap.values()) else
                "scene-checked" if all(v.startswith("available") for v in evidence["maskAvailability"].values()) else "unverified-scene",
            "frequency": frequency, "reasons": ["local-variance-loss", "multi-scale-texture-loss"],
            "algorithmVersion": VERSION}
