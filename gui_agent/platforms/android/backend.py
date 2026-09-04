"""ADB-backed Android implementation of the MVP device backend seam."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from gui_agent.contracts import Observation, PlatformCommand
from gui_agent.platforms.base import DeviceBackend

from .adb_transport import AdbError, AdbTransport


class AndroidBackendError(RuntimeError):
    """A diagnostic Android backend failure that callers must not retry blindly."""


class AndroidDeviceBackend(DeviceBackend):
    """Minimal Android device adapter: observe and execute compiled primitives.

    The action compiler owns normalized-coordinate conversion.  This backend
    accepts pixels only, avoiding any hidden coordinate policy at the device
    boundary.
    """

    _SUPPORTED_COMMANDS = {"tap", "swipe", "text", "launch", "system_key"}

    def __init__(self, transport: AdbTransport, *, screenshot_directory: Path) -> None:
        self._transport = transport
        self._screenshot_directory = screenshot_directory
        self._sequence = 0
        self._closed = False

    @property
    def device_id(self) -> str:
        return self._transport.serial

    def health(self) -> bool:
        return not self._closed and self._transport.is_healthy()

    def observe(self) -> Observation:
        self._assert_open()
        try:
            # Read all values during this method so consumers get one ordered,
            # auditable snapshot rather than querying a device independently.
            width, height = self._transport.screen_size()
            screenshot = self._transport.screenshot_png()
            foreground_app = self._transport.foreground_app()
            self._screenshot_directory.mkdir(parents=True, exist_ok=True)
            screenshot_path = self._screenshot_directory / f"{self._sequence:06d}-{uuid4()}.png"
            screenshot_path.write_bytes(screenshot)
        except (AdbError, OSError) as error:
            raise AndroidBackendError(f"failed to observe Android device {self.device_id}") from error

        observation = Observation(
            device_id=self.device_id,
            sequence=self._sequence,
            screen_width=width,
            screen_height=height,
            screenshot_path=str(screenshot_path),
            foreground_app=foreground_app,
        )
        self._sequence += 1
        return observation

    def execute(self, command: PlatformCommand) -> None:
        self._assert_open()
        if command.name not in self._SUPPORTED_COMMANDS:
            raise AndroidBackendError(f"unsupported Android command: {command.name}")
        try:
            self._execute_arguments(command.name, command.arguments)
        except (AdbError, KeyError, TypeError, ValueError) as error:
            raise AndroidBackendError(
                f"failed Android command {command.name} ({command.validation_id})"
            ) from error

    def _execute_arguments(self, name: str, args: Any) -> None:
        if name == "tap":
            self._transport.tap(int(args["x"]), int(args["y"]))
        elif name == "swipe":
            self._transport.swipe(
                int(args["x1"]), int(args["y1"]), int(args["x2"]), int(args["y2"]),
                int(args.get("duration_ms", 300)),
            )
        elif name == "text":
            self._transport.type_text(str(args["text"]))
        elif name == "launch":
            self._transport.launch(str(args["package_name"]))
        elif name == "system_key":
            self._transport.system_key(str(args["key"]).upper())

    def close(self) -> None:
        self._closed = True

    def _assert_open(self) -> None:
        if self._closed:
            raise AndroidBackendError("Android backend is closed")
