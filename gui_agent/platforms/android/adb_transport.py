# Copyright (c) 2026, 东篱馆主

"""Small, testable wrapper around the Android Debug Bridge executable.

The transport deliberately accepts command arguments as a sequence.  It never
builds a command string or uses a shell on the host, which keeps device serials
and action parameters out of host-shell parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from subprocess import CompletedProcess
import subprocess
from typing import Protocol, Sequence


class AdbError(RuntimeError):
    """Raised when ADB cannot complete a requested operation."""


class CommandRunner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        timeout: float,
        check: bool,
    ) -> CompletedProcess[bytes]: ...


@dataclass(frozen=True)
class AdbDevice:
    """A device reported by ``adb devices``."""

    serial: str
    state: str


class AdbTransport:
    """Run a deliberately small, argument-safe subset of ADB operations."""

    def __init__(
        self,
        serial: str,
        *,
        adb_path: str = "adb",
        timeout_seconds: float = 15.0,
        runner: CommandRunner | None = None,
    ) -> None:
        if not serial or not serial.strip():
            raise ValueError("ADB serial must not be empty")
        self._serial = serial
        self._adb_path = adb_path
        self._timeout_seconds = timeout_seconds
        self._runner = runner or self._subprocess_runner

    @property
    def serial(self) -> str:
        return self._serial

    @staticmethod
    def _subprocess_runner(
        args: Sequence[str], *, capture_output: bool, timeout: float, check: bool
    ) -> CompletedProcess[bytes]:
        return subprocess.run(
            list(args),
            capture_output=capture_output,
            timeout=timeout,
            check=check,
            shell=False,
        )

    @classmethod
    def discover(
        cls,
        *,
        adb_path: str = "adb",
        timeout_seconds: float = 15.0,
        runner: CommandRunner | None = None,
    ) -> list[AdbDevice]:
        invoke = runner or cls._subprocess_runner
        result = cls._run(invoke, [adb_path, "devices"], timeout_seconds)
        devices: list[AdbDevice] = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            if not line or line.startswith("List of devices") or "\t" not in line:
                continue
            serial, state = line.split("\t", maxsplit=1)
            devices.append(AdbDevice(serial=serial, state=state.strip()))
        return devices

    @staticmethod
    def _run(
        runner: CommandRunner, args: Sequence[str], timeout: float
    ) -> CompletedProcess[bytes]:
        try:
            return runner(args, capture_output=True, timeout=timeout, check=True)
        except (OSError, subprocess.SubprocessError) as error:
            raise AdbError(f"ADB command failed: {args[0]}") from error

    def run(self, *arguments: str) -> CompletedProcess[bytes]:
        """Execute an ADB command for this device without a host shell."""
        return self._run(
            self._runner,
            [self._adb_path, "-s", self._serial, *arguments],
            self._timeout_seconds,
        )

    def is_healthy(self) -> bool:
        try:
            return self.run("get-state").stdout.strip() == b"device"
        except AdbError:
            return False

    def screenshot_png(self) -> bytes:
        image = self.run("exec-out", "screencap", "-p").stdout
        if not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise AdbError("ADB returned an invalid screenshot payload")
        return image

    def screen_size(self) -> tuple[int, int]:
        output = self.run("shell", "wm", "size").stdout.decode("utf-8", errors="replace")
        for line in output.splitlines():
            if "Physical size:" not in line and "Override size:" not in line:
                continue
            _, value = line.split(":", maxsplit=1)
            width_text, height_text = value.strip().split("x", maxsplit=1)
            try:
                return int(width_text), int(height_text)
            except ValueError as error:
                raise AdbError(f"unparseable Android screen size: {value!r}") from error
        raise AdbError("ADB did not report an Android screen size")

    def foreground_app(self) -> str | None:
        output = self.run("shell", "dumpsys", "window", "windows").stdout.decode(
            "utf-8", errors="replace"
        )
        marker = "mCurrentFocus="
        for line in output.splitlines():
            if marker not in line:
                continue
            focus = line.split(marker, maxsplit=1)[1].strip()
            if "/" in focus:
                package = focus.split("/", maxsplit=1)[0].split()[-1]
                return package if package else None
        return None

    def tap(self, x: int, y: int) -> None:
        self.run("shell", "input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self.run(
            "shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)
        )

    def type_text(self, text: str) -> None:
        if not text or "\x00" in text or "\n" in text or "\r" in text:
            raise ValueError("text must be non-empty and contain no line breaks or NUL bytes")
        # Android's ``input text`` treats %s as a space.  Parameters remain
        # individual process arguments; no host command string is assembled.
        self.run("shell", "input", "text", text.replace(" ", "%s"))

    def launch(self, package_name: str) -> None:
        if not package_name or any(char.isspace() for char in package_name):
            raise ValueError("package name must be a non-empty token")
        self.run("shell", "monkey", "-p", package_name, "1")

    def system_key(self, keycode: str) -> None:
        if keycode not in {"BACK", "HOME", "RECENTS", "ENTER"}:
            raise ValueError(f"unsupported system key: {keycode}")
        self.run("shell", "input", "keyevent", f"KEYCODE_{keycode}")
