# Copyright (c) 2026, 东篱馆主

"""Android platform adapter for the GUI Agent MVP."""

from .adb_transport import AdbDevice, AdbError, AdbTransport
from .backend import AndroidBackendError, AndroidDeviceBackend
from .device_profile import (
    AndroidDeviceProfile,
    AndroidDeviceProfileError,
    discover_android_device_profile,
)
from .stability import UiStabilityResult, UiStabilityWaiter
from .ui_tree import UiTreeError, find_unique_text_node, parse_ui_tree

__all__ = [
    "AdbDevice",
    "AdbError",
    "AdbTransport",
    "AndroidBackendError",
    "AndroidDeviceBackend",
    "AndroidDeviceProfile",
    "AndroidDeviceProfileError",
    "UiStabilityResult",
    "UiStabilityWaiter",
    "UiTreeError",
    "discover_android_device_profile",
    "find_unique_text_node",
    "parse_ui_tree",
]
