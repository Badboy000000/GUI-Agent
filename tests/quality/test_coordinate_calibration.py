# Copyright (c) 2026, 东篱馆主

"""Offline acceptance coverage for coordinate-convention calibration.

Scripted thousand-mode probes stand in for the model: each reports a raw
model-space answer divided by 999, exactly what the upstream thousand parser
would produce.  Every sample gets a fresh probe instance, so the factory
shares one answer iterator across the probes it creates.  The device side is
a real PNG observation plus a canned UI hierarchy, so no ADB process or model
service is involved.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from gui_agent.brains import (
    CalibrationError,
    CalibrationResult,
    calibrate_coordinate_scale,
)
from gui_agent.contracts import Observation


_WIDTH = 1080
_HEIGHT = 2400

# Four unique clickable targets with a >=40px on-screen margin, plus nodes
# that must be filtered out: a duplicated clickable text, a clickable text
# too close to the edge, and a non-clickable unique text.
_TREE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="com.android.settings" content-desc="" clickable="false"
        bounds="[0,0][1080,2400]">
    <node index="0" text="Wi-Fi" resource-id="com.android.settings:id/wifi"
          class="android.widget.TextView" package="com.android.settings"
          content-desc="" clickable="true" bounds="[100,200][980,320]" />
    <node index="1" text="Bluetooth" resource-id="com.android.settings:id/bt"
          class="android.widget.TextView" package="com.android.settings"
          content-desc="" clickable="true" bounds="[100,400][980,520]" />
    <node index="2" text="Calculator" resource-id="" class="android.widget.TextView"
          package="com.android.settings" content-desc="" clickable="true"
          bounds="[100,600][500,720]" />
    <node index="3" text="Calculator" resource-id="" class="android.widget.TextView"
          package="com.android.settings" content-desc="" clickable="true"
          bounds="[540,600][980,720]" />
    <node index="4" text="Display" resource-id="" class="android.widget.TextView"
          package="com.android.settings" content-desc="" clickable="true"
          bounds="[100,800][980,920]" />
    <node index="5" text="Edge" resource-id="" class="android.widget.TextView"
          package="com.android.settings" content-desc="" clickable="true"
          bounds="[0,1000][200,1080]" />
    <node index="6" text="Label" resource-id="" class="android.widget.TextView"
          package="com.android.settings" content-desc="" clickable="false"
          bounds="[100,1150][500,1230]" />
    <node index="7" text="Hotspot" resource-id="" class="android.widget.TextView"
          package="com.android.settings" content-desc="" clickable="true"
          bounds="[100,1300][980,1420]" />
  </node>
</hierarchy>"""

# Pixel-truth centers of the four eligible targets, in document order.
_WIFI_CENTER = (540.0, 260.0)
_BLUETOOTH_CENTER = (540.0, 460.0)
_DISPLAY_CENTER = (540.0, 860.0)
_HOTSPOT_CENTER = (540.0, 1360.0)


class FakeTransport:
    """Serves one canned hierarchy dump per call."""

    def __init__(self, xml_text: str) -> None:
        self._xml_text = xml_text
        self.dumps = 0

    def dump_ui_hierarchy(self) -> str:
        self.dumps += 1
        return self._xml_text


class FakeBackend:
    """A device whose observation is a real PNG on disk."""

    device_id = "offline-android"

    def __init__(self, tmp_path: Path) -> None:
        screenshot_path = tmp_path / "calibration-screen.png"
        Image.new("RGB", (_WIDTH, _HEIGHT), (10, 20, 30)).save(screenshot_path)
        self._observation = Observation(
            device_id=self.device_id,
            sequence=0,
            screen_width=_WIDTH,
            screen_height=_HEIGHT,
            screenshot_path=str(screenshot_path),
            foreground_app="com.android.settings",
        )
        self.observe_calls = 0

    def observe(self) -> Observation:
        self.observe_calls += 1
        return self._observation


class FakeProbe:
    """A thousand-mode probe wrapping a model with scripted raw answers."""

    def __init__(self, raw_answers: Iterator[Any]) -> None:
        self._raw_answers = raw_answers
        self.instructions: list[str] = []
        self.screenshot_sizes: list[tuple[int, int]] = []

    def predict(self, instruction: str, obs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        screenshot = obs["screenshot"]
        assert isinstance(screenshot, Image.Image)
        self.instructions.append(instruction)
        self.screenshot_sizes.append(screenshot.size)
        raw = next(self._raw_answers)
        if isinstance(raw, dict):
            return "raw probe response", raw
        return "raw probe response", {
            "action": "click",
            "coordinate": [raw[0] / 999, raw[1] / 999],
        }


class FakeProbeFactory:
    """Creates fresh probes that share one scripted answer iterator."""

    def __init__(self, raw_points: list[Any]) -> None:
        self._raw_answers = iter(raw_points)
        self.overrides: list[Mapping[str, Any]] = []
        self.probes: list[FakeProbe] = []

    def __call__(self, runtime_conf_override: Mapping[str, Any]) -> FakeProbe:
        self.overrides.append(dict(runtime_conf_override))
        probe = FakeProbe(self._raw_answers)
        self.probes.append(probe)
        return probe


def _calibrate(
    tmp_path: Path, raw_points: list[Any], xml_text: str = _TREE_XML
) -> tuple[CalibrationResult, FakeProbeFactory, FakeBackend, FakeTransport]:
    backend = FakeBackend(tmp_path)
    transport = FakeTransport(xml_text)
    factory = FakeProbeFactory(raw_points)
    result = calibrate_coordinate_scale(backend, transport, factory)
    return result, factory, backend, transport


def test_pixel_answering_model_snaps_to_pixels(tmp_path: Path) -> None:
    result, factory, backend, transport = _calibrate(
        tmp_path, [_WIFI_CENTER, _BLUETOOTH_CENTER]
    )

    assert result.coordinate_scale == "pixels"
    assert result.scale_x is None and result.scale_y is None
    assert result.samples == 2
    # Calibration stays read-only: one observation, one dump, model calls only.
    assert backend.observe_calls == 1
    assert transport.dumps == 1
    # Each sample comes from a fresh probe instance fixed to thousand mode.
    assert factory.overrides == [{"coordinate_scale": "thousand"}] * 2
    assert len(factory.probes) == 2
    assert factory.probes[0] is not factory.probes[1]
    assert "discarded 0 implausible sample(s)" in result.detail


def test_thousand_answering_model_snaps_to_thousand(tmp_path: Path) -> None:
    raw = [
        (cx / _WIDTH * 999, cy / _HEIGHT * 999)
        for cx, cy in (_WIFI_CENTER, _BLUETOOTH_CENTER)
    ]

    result, _, _, _ = _calibrate(tmp_path, raw)

    assert result.coordinate_scale == "thousand"
    assert result.scale_x is None and result.scale_y is None


def test_normalized_answering_model_becomes_explicit_unit_extents(tmp_path: Path) -> None:
    raw = [
        (cx / _WIDTH, cy / _HEIGHT)
        for cx, cy in (_WIFI_CENTER, _BLUETOOTH_CENTER)
    ]

    result, _, _, _ = _calibrate(tmp_path, raw)

    assert result.coordinate_scale == "explicit"
    assert result.scale_x == pytest.approx(1.0)
    assert result.scale_y == pytest.approx(1.0)


def test_one_and_a_half_scaled_pixels_adopt_free_explicit_extents(tmp_path: Path) -> None:
    raw = [
        (1.5 * cx, 1.5 * cy)
        for cx, cy in (_WIFI_CENTER, _BLUETOOTH_CENTER)
    ]

    result, _, _, _ = _calibrate(tmp_path, raw)

    assert result.coordinate_scale == "explicit"
    assert result.scale_x == pytest.approx(1.5 * _WIDTH, rel=1e-6)
    assert result.scale_y == pytest.approx(1.5 * _HEIGHT, rel=1e-6)


def test_random_answering_model_fails_calibration(tmp_path: Path) -> None:
    raw = [(100.0, 200.0), (900.0, 1500.0), (50.0, 60.0), (700.0, 800.0)]

    with pytest.raises(CalibrationError, match="no consistent sample pair"):
        _calibrate(tmp_path, raw)


def test_inconsistent_samples_fail_calibration(tmp_path: Path) -> None:
    raw = [
        _WIFI_CENTER,
        (1.3 * _BLUETOOTH_CENTER[0], 1.3 * _BLUETOOTH_CENTER[1]),
        (1.6 * _DISPLAY_CENTER[0], 1.6 * _DISPLAY_CENTER[1]),
        (2.1 * _HOTSPOT_CENTER[0], 2.1 * _HOTSPOT_CENTER[1]),
    ]

    with pytest.raises(CalibrationError, match="no consistent sample pair"):
        _calibrate(tmp_path, raw)


def test_calibration_fails_without_an_eligible_target(tmp_path: Path) -> None:
    xml_text = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="Same" resource-id="" class="android.widget.TextView"
        package="com.demo" content-desc="" clickable="true" bounds="[100,200][500,320]">
    <node index="0" text="Same" resource-id="" class="android.widget.TextView"
          package="com.demo" content-desc="" clickable="true" bounds="[100,400][500,520]" />
  </node>
</hierarchy>"""

    with pytest.raises(CalibrationError, match="no unique clickable"):
        _calibrate(tmp_path, [_WIFI_CENTER, _BLUETOOTH_CENTER], xml_text=xml_text)


def test_unusable_probe_answers_exhaust_candidates_and_fail(tmp_path: Path) -> None:
    raw = [
        {"action": None},
        {"action": "wait"},
        {"action": "click"},
        {"action": "click", "coordinate": ["north", "south"]},
    ]

    with pytest.raises(CalibrationError, match="no consistent sample pair"):
        _calibrate(tmp_path, raw)


def test_consistent_samples_come_from_different_targets(tmp_path: Path) -> None:
    _, factory, _, _ = _calibrate(tmp_path, [_WIFI_CENTER, _BLUETOOTH_CENTER])

    first, second = factory.probes
    assert first.instructions == ['Click the element labeled "Wi-Fi"']
    assert second.instructions == ['Click the element labeled "Bluetooth"']
    assert first.instructions != second.instructions
    assert first.screenshot_sizes == [(_WIDTH, _HEIGHT)]
    assert second.screenshot_sizes == [(_WIDTH, _HEIGHT)]


def test_an_implausible_sample_is_discarded_and_a_later_pair_is_adopted(
    tmp_path: Path,
) -> None:
    # The first answer points at the wrong element: its implied extent snaps
    # to no convention and falls outside the sanity bounds, so it is dropped.
    garbage = (50.0, 30.0)
    result, factory, _, _ = _calibrate(
        tmp_path, [garbage, _BLUETOOTH_CENTER, _DISPLAY_CENTER]
    )

    assert result.coordinate_scale == "pixels"
    assert result.samples == 2
    assert "discarded 1 implausible sample(s)" in result.detail
    assert len(factory.probes) == 3
    assert [probe.instructions[0] for probe in factory.probes] == [
        'Click the element labeled "Wi-Fi"',
        'Click the element labeled "Bluetooth"',
        'Click the element labeled "Display"',
    ]


def test_four_implausible_samples_fail_calibration(tmp_path: Path) -> None:
    raw = [(50.0, 30.0)] * 4

    with pytest.raises(CalibrationError, match="no consistent sample pair"):
        _calibrate(tmp_path, raw)


def test_consistent_but_implausible_samples_are_dropped_by_the_prefilter(
    tmp_path: Path,
) -> None:
    # Every answer sits at 1% of the truth: the implied extents (S/dim = 0.01,
    # far below the 0.25 sanity floor) agree with each other perfectly, so the
    # pair check alone would adopt them.  The prefilter must discard all four
    # before pairing is ever consulted.
    raw = [
        (0.01 * cx, 0.01 * cy)
        for cx, cy in (_WIFI_CENTER, _BLUETOOTH_CENTER, _DISPLAY_CENTER, _HOTSPOT_CENTER)
    ]

    with pytest.raises(CalibrationError) as excinfo:
        _calibrate(tmp_path, raw)

    message = str(excinfo.value)
    assert "no consistent sample pair" in message
    assert "discarded 4 implausible sample(s)" in message
    # The failure must not come from the adoption-time sanity guard.
    assert "implausible for this screen" not in message
