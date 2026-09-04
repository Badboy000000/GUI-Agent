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
    "discover_android_device_profile",
]
