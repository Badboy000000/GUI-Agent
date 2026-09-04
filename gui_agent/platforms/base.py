"""Stable seam between task orchestration and a concrete device platform."""

from __future__ import annotations

from abc import ABC, abstractmethod

from gui_agent.contracts import Observation, PlatformCommand


class DeviceBackend(ABC):
    """A connected device capable of one observation and safe primitives.

    Backends receive only validated, compiled commands.  They do not accept a
    model proposal and never decide whether a side effect is allowed.
    """

    @property
    @abstractmethod
    def device_id(self) -> str: ...

    @abstractmethod
    def health(self) -> bool:
        """Return whether the existing device connection is usable."""

    @abstractmethod
    def observe(self) -> Observation:
        """Persist and return one coherent device observation."""

    @abstractmethod
    def execute(self, command: PlatformCommand) -> None:
        """Perform one platform command or raise a diagnostic exception."""

    @abstractmethod
    def close(self) -> None:
        """Release resources owned by this backend."""
