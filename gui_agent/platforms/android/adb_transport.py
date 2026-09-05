# Copyright (c) 2026, 东篱馆主

"""Small, testable wrapper around the Android Debug Bridge executable.

The transport deliberately accepts command arguments as a sequence.  It never
builds a command string or uses a shell on the host, which keeps device serials
and action parameters out of host-shell parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from subprocess import CompletedProcess
import subprocess
from typing import Protocol, Sequence
from uuid import uuid4


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

    _COMPONENT_PATTERN = re.compile(
        r"(?P<package>[A-Za-z][A-Za-z0-9_.]*)/(?P<activity>[A-Za-z0-9_.$]+)"
    )
    _SETTINGS_ACTION = "android.settings.SETTINGS"
    _HOME_ACTION = "android.intent.action.MAIN"
    _HOME_CATEGORY = "android.intent.category.HOME"

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

    def dump_ui_hierarchy(self) -> str:
        """Dump the current UI hierarchy via uiautomator and return the raw XML.

        The dump is written to a unique device path per call so concurrent
        captures cannot clobber each other, read back through ``exec-out``,
        and removed afterwards; a cleanup failure never masks the result.
        """

        device_path = f"/data/local/tmp/gui_agent_ui_{uuid4().hex}.xml"
        self.run("shell", "uiautomator", "dump", device_path)
        try:
            payload = self.run("exec-out", "cat", device_path).stdout
        finally:
            try:
                self.run("shell", "rm", "-f", device_path)
            except AdbError:
                pass
        xml_text = payload.decode("utf-8", errors="replace")
        if not xml_text.strip() or "<hierarchy" not in xml_text:
            raise AdbError("ADB returned an invalid UI hierarchy payload")
        return xml_text

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

    def manufacturer(self) -> str:
        """Return the device-reported manufacturer for a read-only preflight."""

        return self._system_property("ro.product.manufacturer")

    def model(self) -> str:
        """Return the device-reported model for a read-only preflight."""

        return self._system_property("ro.product.model")

    def android_release(self) -> str:
        """Return the device-reported Android release for a read-only preflight."""

        return self._system_property("ro.build.version.release")

    def settings_package(self) -> str | None:
        """Resolve the installed handler for the standard Settings intent."""

        return self._resolve_activity_package(self._SETTINGS_ACTION)

    def home_package(self) -> str | None:
        """Resolve the current handler for the standard HOME intent."""

        return self._resolve_activity_package(self._HOME_ACTION, category=self._HOME_CATEGORY)

    def foreground_app(self) -> str | None:
        """Return the foreground package across legacy and current Android output.

        Older releases expose ``mCurrentFocus`` from WindowManager.  Android 14
        images and several OEM builds omit it, but report the resumed activity
        from ActivityManager instead.  All fallbacks remain observation-only.
        """

        window_output = self.run("shell", "dumpsys", "window", "windows").stdout.decode(
            "utf-8", errors="replace"
        )
        marker = "mCurrentFocus="
        for line in window_output.splitlines():
            if marker not in line:
                continue
            package = self._package_from_component(line.split(marker, maxsplit=1)[1])
            if package is not None:
                return package

        activity_output = self.run("shell", "dumpsys", "activity", "activities").stdout.decode(
            "utf-8", errors="replace"
        )
        for marker in ("topResumedActivity=", "mResumedActivity:", "mResumedActivity="):
            for line in activity_output.splitlines():
                if marker not in line:
                    continue
                package = self._package_from_component(line.split(marker, maxsplit=1)[1])
                if package is not None:
                    return package
        return None

    def _system_property(self, name: str) -> str:
        value = self.run("shell", "getprop", name).stdout.decode("utf-8", errors="replace").strip()
        if not value:
            raise AdbError(f"ADB did not report Android property {name}")
        return value

    def _resolve_activity_package(self, action: str, *, category: str | None = None) -> str | None:
        arguments = ["shell", "cmd", "package", "resolve-activity", "--brief", "-a", action]
        if category is not None:
            arguments.extend(("-c", category))
        output = self.run(*arguments).stdout.decode("utf-8", errors="replace")
        return self._package_from_component(output)

    @classmethod
    def _package_from_component(cls, text: str) -> str | None:
        packages = {match.group("package") for match in cls._COMPONENT_PATTERN.finditer(text)}
        if len(packages) != 1:
            return None
        return next(iter(packages))

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
