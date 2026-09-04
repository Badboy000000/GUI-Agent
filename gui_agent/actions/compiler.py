"""Android action compilation with explicit coordinate and app-name policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from gui_agent.contracts import Observation, PlatformCommand, ValidatedAction


class ActionCompilationError(ValueError):
    """Raised when a valid model action has no safe Android equivalent."""


@dataclass(frozen=True, slots=True)
class AndroidActionCompiler:
    """Translate approved mobile actions into pixel-based Android commands.

    App names are intentionally resolved only through the configured mapping;
    arbitrary model text must not become an Android package invocation.
    """

    app_packages: Mapping[str, str] = field(default_factory=dict)
    swipe_distance_ratio: float = 0.35

    def compile(self, action: ValidatedAction, observation: Observation) -> PlatformCommand:
        if action.source_observation_id != observation.id:
            raise ActionCompilationError("cannot compile action for a stale observation")

        args = action.arguments
        if action.name == "click":
            x, y = self._pixel_point(args["coordinate"], observation)
            return PlatformCommand("tap", {"x": x, "y": y}, action.validation_id)
        if action.name == "long_press":
            x, y = self._pixel_point(args["coordinate"], observation)
            return PlatformCommand(
                "swipe", {"x1": x, "y1": y, "x2": x, "y2": y, "duration_ms": 600}, action.validation_id
            )
        if action.name == "drag":
            x1, y1 = self._pixel_point(args["start_coordinate"], observation)
            x2, y2 = self._pixel_point(args["end_coordinate"], observation)
            return PlatformCommand("swipe", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": 300}, action.validation_id)
        if action.name == "swipe":
            x1, y1, x2, y2 = self._swipe_points(args, observation)
            return PlatformCommand("swipe", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": 300}, action.validation_id)
        if action.name == "type":
            return PlatformCommand("text", {"text": args["text"]}, action.validation_id)
        if action.name == "open":
            package_name = self.app_packages.get(args["text"])
            if package_name is None:
                raise ActionCompilationError(f"app is not allowlisted: {args['text']!r}")
            return PlatformCommand("launch", {"package_name": package_name}, action.validation_id)
        if action.name == "system_button":
            key_map = {"back": "BACK", "home": "HOME", "menu": "RECENTS", "enter": "ENTER"}
            return PlatformCommand("system_key", {"key": key_map[args["button"]]}, action.validation_id)
        raise ActionCompilationError(f"action has no Android device command: {action.name}")

    @staticmethod
    def _pixel_point(point: object, observation: Observation) -> tuple[int, int]:
        x_normalized, y_normalized = point  # validated before compilation
        return (
            round(float(x_normalized) * (observation.screen_width - 1)),
            round(float(y_normalized) * (observation.screen_height - 1)),
        )

    def _swipe_points(self, args: Mapping[str, object], observation: Observation) -> tuple[int, int, int, int]:
        origin = args.get("coordinate", [0.5, 0.5])
        x1, y1 = self._pixel_point(origin, observation)
        distance = round(min(observation.screen_width, observation.screen_height) * self.swipe_distance_ratio)
        direction = args["direction"]
        deltas = {"up": (0, -distance), "down": (0, distance), "left": (-distance, 0), "right": (distance, 0)}
        delta_x, delta_y = deltas[direction]
        x2 = min(max(x1 + delta_x, 0), observation.screen_width - 1)
        y2 = min(max(y1 + delta_y, 0), observation.screen_height - 1)
        return x1, y1, x2, y2
