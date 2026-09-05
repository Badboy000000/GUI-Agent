# Copyright (c) 2026, 东篱馆主

"""One-shot coordinate-convention calibration against the live UI tree.

The model answers a probe instruction in its own coordinate space.  The UI
tree supplies the pixel truth for the probe target, so the model-space extent
``S`` is directly measurable: ``S_x = W * raw_x / cx`` and
``S_y = H * raw_y / cy``.  ``pixels``/``thousand``/``normalized`` conventions
are the special cases ``S = (W, H)`` / ``(999, 999)`` / ``(1, 1)``; a measured
extent that matches none of them is adopted as explicit coefficients after a
sanity bound.

Sampling is fault-tolerant: up to four samples are taken, each from a fresh
probe instance on a different target, and any mutually consistent pair is
adopted.  The whole procedure is read-only for the device: one observation,
one hierarchy dump, and model calls.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from PIL import Image

from gui_agent.platforms.android import (
    AdbError,
    AdbTransport,
    AndroidBackendError,
    UiTreeError,
    find_unique_text_node,
    parse_ui_tree,
)
from gui_agent.platforms.base import DeviceBackend


class CalibrationError(RuntimeError):
    """Raised when the coordinate convention cannot be measured fail-closed."""


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    """The measured coordinate convention for one (model, endpoint, device).

    ``samples`` records how many probe samples were actually adopted; the
    ``detail`` notes how many implausible samples were discarded along the way.
    """

    coordinate_scale: str
    scale_x: float | None
    scale_y: float | None
    samples: int
    detail: str


class _ProbePredictor(Protocol):
    def predict(self, instruction: str, obs: dict[str, Any], **kwargs: Any) -> tuple[str, dict[str, Any]]: ...


ProbePredictorFactory = Callable[[Mapping[str, Any]], _ProbePredictor]

_THOUSAND = 999
_MIN_MARGIN_PX = 40
_MAX_SAMPLES = 4
_SNAP_TOLERANCE = 0.10
_CONSISTENCY_TOLERANCE = 0.05
_SANITY_MIN_RATIO = 0.25
_SANITY_MAX_RATIO = 4.0


def calibrate_coordinate_scale(
    backend: DeviceBackend,
    transport: AdbTransport,
    probe_predictor_factory: ProbePredictorFactory,
    *,
    samples: int = 2,
) -> CalibrationResult:
    """Measure the model's coordinate convention and return it fail-closed.

    Up to four samples are probed, each on a different candidate target.  A
    sample whose implied extent snaps to no convention and falls outside the
    sanity bounds is implausible (the model answered the wrong element) and is
    discarded immediately; the first mutually consistent group of ``samples``
    retained samples is adopted by mean.

    Every sample uses a fresh probe instance: ``predict`` appends to the
    agent's own trajectory memory, so reusing one probe would let an earlier
    sample steer a later one through history, and reusing the production
    agent would leak calibration exchanges into the official task history.
    """

    if samples < 2:
        raise ValueError("calibration needs at least two samples for the consistency check")
    try:
        observation = backend.observe()
        tree = parse_ui_tree(transport.dump_ui_hierarchy())
    except (AdbError, AndroidBackendError, UiTreeError) as error:
        raise CalibrationError("unable to capture the calibration observation") from error

    width, height = observation.screen_width, observation.screen_height
    targets = _calibration_targets(tree, width, height)
    if not targets:
        raise CalibrationError("no unique clickable text node with a safe on-screen margin")
    if observation.screenshot_path is None:
        raise CalibrationError("calibration requires an observation screenshot")
    screenshot = _load_screenshot(observation.screenshot_path)

    retained: list[tuple[float, float]] = []
    discarded = 0
    attempts = 0
    for target in targets:
        if attempts >= _MAX_SAMPLES:
            break
        attempts += 1
        probe = probe_predictor_factory({"coordinate_scale": "thousand"})
        raw = _probe_target(probe, str(target["text"]), screenshot)
        if raw is None:
            continue
        center_x = (target["bounds"][0] + target["bounds"][2]) / 2
        center_y = (target["bounds"][1] + target["bounds"][3]) / 2
        sample = (width * raw[0] / center_x, height * raw[1] / center_y)
        if _nearest_convention(*sample, width, height) is None and not _in_sanity_bounds(
            *sample, width, height
        ):
            discarded += 1
            continue
        agreeing = [prior for prior in retained if _extents_agree(prior, sample)]
        if len(agreeing) >= samples - 1:
            adopted = [sample, *agreeing[: samples - 1]]
            scale_x = sum(point[0] for point in adopted) / len(adopted)
            scale_y = sum(point[1] for point in adopted) / len(adopted)
            return _adopt(scale_x, scale_y, width, height, len(adopted), discarded)
        retained.append(sample)
    raise CalibrationError(
        f"no consistent sample pair across {attempts} calibration probe(s)"
        f"; discarded {discarded} implausible sample(s)"
    )


def _calibration_targets(tree: dict[str, Any], width: int, height: int) -> list[dict[str, Any]]:
    """Clickable nodes with a tree-unique text and a safe on-screen margin."""

    targets = []
    for node in tree["nodes"]:
        if not node["clickable"] or not node["text"]:
            continue
        if find_unique_text_node(tree, node["text"]) is not node:
            continue
        left, top, right, bottom = node["bounds"]
        if (
            left < _MIN_MARGIN_PX
            or top < _MIN_MARGIN_PX
            or right > width - _MIN_MARGIN_PX
            or bottom > height - _MIN_MARGIN_PX
        ):
            continue
        targets.append(node)
    return targets


def _probe_target(
    probe: _ProbePredictor, text: str, screenshot: Image.Image
) -> tuple[float, float] | None:
    """Ask the probe to click ``text`` and reconstruct its raw coordinates.

    The probe is fixed to the resolution-independent thousand convention, so
    its parsed [0, 1] answer multiplies back to the model's raw space exactly.
    """

    try:
        _, action = probe.predict(
            f'Click the element labeled "{text}"',
            {"screenshot": screenshot, "accessibility_tree": None},
        )
    except Exception:
        return None
    if not isinstance(action, dict):
        return None
    coordinate = action.get("coordinate")
    if (
        not isinstance(coordinate, (list, tuple))
        or len(coordinate) != 2
        or any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in coordinate)
    ):
        return None
    return (float(coordinate[0]) * _THOUSAND, float(coordinate[1]) * _THOUSAND)


def _nearest_convention(scale_x: float, scale_y: float, width: int, height: int) -> str | None:
    """The closest convention when both axes deviate by less than the snap tolerance."""

    candidates = {
        "pixels": (float(width), float(height)),
        "thousand": (float(_THOUSAND), float(_THOUSAND)),
        "normalized": (1.0, 1.0),
    }
    nearest: tuple[float, str] | None = None
    for name, (candidate_x, candidate_y) in candidates.items():
        deviation = max(
            abs(scale_x - candidate_x) / candidate_x,
            abs(scale_y - candidate_y) / candidate_y,
        )
        if deviation < _SNAP_TOLERANCE and (nearest is None or deviation < nearest[0]):
            nearest = (deviation, name)
    return nearest[1] if nearest is not None else None


def _in_sanity_bounds(scale_x: float, scale_y: float, width: int, height: int) -> bool:
    """Whether the implied extent is plausible relative to the screen size."""

    return (
        _SANITY_MIN_RATIO <= scale_x / width <= _SANITY_MAX_RATIO
        and _SANITY_MIN_RATIO <= scale_y / height <= _SANITY_MAX_RATIO
    )


def _extents_agree(first: tuple[float, float], second: tuple[float, float]) -> bool:
    """Two samples agree when both axes stay within the consistency tolerance."""

    return all(
        _relative_difference(a, b) < _CONSISTENCY_TOLERANCE
        for a, b in zip(first, second)
    )


def _relative_difference(first: float, second: float) -> float:
    baseline = (abs(first) + abs(second)) / 2
    if baseline == 0:
        return 0.0
    return abs(first - second) / baseline


def _adopt(
    scale_x: float,
    scale_y: float,
    width: int,
    height: int,
    samples: int,
    discarded: int,
) -> CalibrationResult:
    suffix = f"; discarded {discarded} implausible sample(s)"
    convention = _nearest_convention(scale_x, scale_y, width, height)
    if convention == "normalized":
        return CalibrationResult(
            coordinate_scale="explicit",
            scale_x=1.0,
            scale_y=1.0,
            samples=samples,
            detail="model answers in normalized [0, 1] coordinates; adopted explicit unit extents"
            + suffix,
        )
    if convention is not None:
        return CalibrationResult(
            coordinate_scale=convention,
            scale_x=None,
            scale_y=None,
            samples=samples,
            detail=f"model answers in {convention} coordinates" + suffix,
        )
    if not _in_sanity_bounds(scale_x, scale_y, width, height):
        raise CalibrationError("measured model coordinate space is implausible for this screen")
    return CalibrationResult(
        coordinate_scale="explicit",
        scale_x=scale_x,
        scale_y=scale_y,
        samples=samples,
        detail="adopted measured model coordinate space extents" + suffix,
    )


def _load_screenshot(screenshot_path: str) -> Image.Image:
    try:
        with Image.open(screenshot_path) as image:
            return image.convert("RGB").copy()
    except (OSError, ValueError) as error:
        raise CalibrationError("unable to load the calibration screenshot") from error
