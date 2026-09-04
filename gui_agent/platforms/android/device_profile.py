# Copyright (c) 2026, 东篱馆主

"""Read-only, fail-closed Android device facts for P3 evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass

from .adb_transport import AdbError, AdbTransport


class AndroidDeviceProfileError(RuntimeError):
    """Raised when a device cannot provide the facts required for safe P3 runs."""


@dataclass(frozen=True, slots=True)
class AndroidDeviceProfile:
    """Immutable Android facts captured before an evaluation task starts.

    Package names are resolved from the connected device rather than inferred
    from its manufacturer.  The profile contains no credentials or device input
    commands and does not reset any device state.
    """

    serial: str
    manufacturer: str
    model: str
    android_release: str
    screen_width: int
    screen_height: int
    settings_package: str
    home_package: str
    foreground_package: str


def discover_android_device_profile(transport: AdbTransport) -> AndroidDeviceProfile:
    """Read the P3 preflight facts or fail before any task input can be sent."""

    if not transport.is_healthy():
        raise AndroidDeviceProfileError(f"Android device {transport.serial!r} is not healthy")

    try:
        screen_width, screen_height = transport.screen_size()
        settings_package = transport.settings_package()
        home_package = transport.home_package()
        foreground_package = transport.foreground_app()
        missing_fact = next(
            (
                name
                for name, value in (
                    ("Settings package", settings_package),
                    ("HOME package", home_package),
                    ("foreground package", foreground_package),
                )
                if value is None
            ),
            None,
        )
        if missing_fact is not None:
            raise AndroidDeviceProfileError(f"Android preflight requires an observable {missing_fact}")
        return AndroidDeviceProfile(
            serial=transport.serial,
            manufacturer=transport.manufacturer(),
            model=transport.model(),
            android_release=transport.android_release(),
            screen_width=screen_width,
            screen_height=screen_height,
            settings_package=settings_package,
            home_package=home_package,
            foreground_package=foreground_package,
        )
    except AdbError as error:
        raise AndroidDeviceProfileError(
            f"failed to collect Android preflight facts for {transport.serial!r}"
        ) from error
