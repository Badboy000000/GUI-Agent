# Copyright (c) 2026, 东篱馆主

"""Local append-only audit events and data-only JSONL replay.

Audit payloads are deliberately limited to JSON values.  Callers must record
only safe metadata, IDs, and artifact paths: never screenshot/image content,
raw typed input, credentials, tokens, cookies, or other sensitive text.

``load_replay`` only parses and validates historical event data.  This module
does not import a platform backend and exposes no API that can execute a
device command.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Protocol, TypeAlias, cast

from gui_agent.contracts.task import TaskId


JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]
JSONPayload: TypeAlias = Mapping[str, JSONValue]
_JSON_RECORD_VERSION = 1


class AuditError(ValueError):
    """Base exception for invalid audit data or an unavailable audit sink."""


class AuditPayloadError(AuditError):
    """Raised when a caller tries to record a value outside the JSON contract."""


class AuditWriteError(AuditError):
    """Raised when an audit event cannot be appended durably enough to continue."""


class AuditReplayError(AuditError):
    """Raised when a JSONL audit trail is malformed or internally inconsistent."""


class AuditEventKind(str, Enum):
    """Events the Android task runner may record during a P2 task."""

    TASK_STARTED = "task_started"
    DEVICE_HEALTH_CHECKED = "device_health_checked"
    OBSERVATION_RECEIVED = "observation_received"
    ACTION_PROPOSED = "action_proposed"
    ACTION_VALIDATED = "action_validated"
    COMMAND_EXECUTED = "command_executed"
    CONFIRMATION_REQUESTED = "confirmation_requested"
    CONFIRMATION_APPROVED = "confirmation_approved"
    HUMAN_TAKEOVER = "human_takeover"
    STABILITY_WAIT_COMPLETED = "stability_wait_completed"
    STABILITY_WAIT_TIMEOUT = "stability_wait_timeout"
    TASK_FINISHED = "task_finished"
    TASK_ERROR = "task_error"


class AuditRecorder(Protocol):
    """A synchronous recorder seam for orchestration.

    The runner supplies its task ID and an ``AuditEventKind``.  The recorder
    raises on failure so the runner can stop rather than silently losing a
    safety-relevant event.
    """

    def record(
        self,
        task_id: TaskId | str,
        kind: AuditEventKind,
        payload: JSONPayload,
    ) -> "AuditEvent": ...


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One normalized, ordered audit record suitable for JSONL persistence."""

    sequence: int
    occurred_at: datetime
    task_id: TaskId
    kind: AuditEventKind
    payload: JSONPayload

    def __post_init__(self) -> None:
        if isinstance(self.sequence, bool) or not isinstance(self.sequence, int):
            raise AuditReplayError("audit event sequence must be an integer")
        if self.sequence < 0:
            raise AuditReplayError("audit event sequence must be non-negative")
        object.__setattr__(self, "occurred_at", _utc_timestamp(self.occurred_at))
        object.__setattr__(self, "task_id", _task_id(self.task_id))
        if not isinstance(self.kind, AuditEventKind):
            raise AuditReplayError("audit event kind must be an AuditEventKind")
        object.__setattr__(self, "payload", _normalize_payload(self.payload))

    def to_json_record(self) -> dict[str, JSONValue]:
        """Return the versioned JSON object written as one JSONL line."""

        return {
            "schema_version": _JSON_RECORD_VERSION,
            "sequence": self.sequence,
            "occurred_at": self.occurred_at.isoformat(),
            "task_id": str(self.task_id),
            "kind": self.kind.value,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_json_record(cls, record: object) -> "AuditEvent":
        """Parse one strict, versioned JSON object without performing I/O."""

        if not isinstance(record, dict):
            raise AuditReplayError("audit JSONL line must contain an object")
        required_fields = {
            "schema_version",
            "sequence",
            "occurred_at",
            "task_id",
            "kind",
            "payload",
        }
        actual_fields = set(record)
        if actual_fields != required_fields:
            raise AuditReplayError("audit event fields do not match the supported schema")
        if record["schema_version"] != _JSON_RECORD_VERSION:
            raise AuditReplayError("unsupported audit event schema version")
        occurred_at = _parse_timestamp(record["occurred_at"])
        try:
            kind = AuditEventKind(record["kind"])
        except (TypeError, ValueError) as error:
            raise AuditReplayError("unsupported audit event kind") from error
        try:
            return cls(
                sequence=cast(int, record["sequence"]),
                occurred_at=occurred_at,
                task_id=_task_id(record["task_id"]),
                kind=kind,
                payload=cast(JSONPayload, record["payload"]),
            )
        except AuditError:
            raise
        except (TypeError, ValueError) as error:
            raise AuditReplayError("invalid audit event") from error


@dataclass(frozen=True, slots=True)
class AuditReplay:
    """Validated historical events; intentionally a data container only."""

    task_id: TaskId
    events: tuple[AuditEvent, ...]


class JsonlAuditRecorder:
    """Append one task's events to a local JSONL file, flushing each record.

    Existing non-empty files are validated before appending.  A recorder binds
    to the first task ID it sees, preventing an accidental interleaving of two
    task trails in one file.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        task_id: TaskId | str | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(path)
        self._clock = clock or _now_utc
        self._task_id = _task_id(task_id) if task_id is not None else None
        self._next_sequence = 0

        if self._path.exists() and self._path.stat().st_size:
            replay = load_replay(self._path, task_id=self._task_id)
            self._task_id = replay.task_id
            self._next_sequence = len(replay.events)

    @property
    def path(self) -> Path:
        """The append-only local audit file managed by this recorder."""

        return self._path

    def record(
        self,
        task_id: TaskId | str,
        kind: AuditEventKind,
        payload: JSONPayload,
    ) -> AuditEvent:
        """Append and flush an event, raising instead of dropping audit data."""

        normalized_task_id = _task_id(task_id)
        if not isinstance(kind, AuditEventKind):
            raise AuditPayloadError("audit event kind must be an AuditEventKind")
        if self._task_id is not None and normalized_task_id != self._task_id:
            raise AuditWriteError("an audit file may contain events for only one task ID")

        event = AuditEvent(
            sequence=self._next_sequence,
            occurred_at=self._clock(),
            task_id=normalized_task_id,
            kind=kind,
            payload=payload,
        )
        encoded = json.dumps(
            event.to_json_record(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(f"{encoded}\n")
                file.flush()
        except OSError as error:
            raise AuditWriteError(f"cannot append audit event to {self._path}") from error

        self._task_id = normalized_task_id
        self._next_sequence += 1
        return event


def load_replay(path: str | Path, *, task_id: TaskId | str | None = None) -> AuditReplay:
    """Restore and validate a JSONL trail without invoking any device action."""

    audit_path = Path(path)
    expected_task_id = _task_id(task_id) if task_id is not None else None
    try:
        with audit_path.open("r", encoding="utf-8") as file:
            lines = list(file)
    except OSError as error:
        raise AuditReplayError(f"cannot read audit trail {audit_path}") from error
    if not lines:
        raise AuditReplayError("audit trail must contain at least one event")

    events: list[AuditEvent] = []
    seen_task_id: TaskId | None = None
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise AuditReplayError(f"empty audit JSONL line at line {line_number}")
        try:
            raw_record = json.loads(line, parse_constant=_reject_json_constant)
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise AuditReplayError(f"invalid audit JSON at line {line_number}") from error
        try:
            event = AuditEvent.from_json_record(raw_record)
        except AuditError as error:
            raise AuditReplayError(f"invalid audit event at line {line_number}: {error}") from error
        if seen_task_id is None:
            seen_task_id = event.task_id
        elif event.task_id != seen_task_id:
            raise AuditReplayError(f"audit task ID changes at line {line_number}")
        if event.sequence != len(events):
            raise AuditReplayError(
                f"audit sequence must be contiguous from zero; line {line_number} has {event.sequence}"
            )
        events.append(event)

    if seen_task_id is None:  # Defensive; the non-empty check above makes this unreachable.
        raise AuditReplayError("audit trail must contain at least one event")
    if expected_task_id is not None and seen_task_id != expected_task_id:
        raise AuditReplayError("audit task ID does not match the expected task")
    return AuditReplay(task_id=seen_task_id, events=tuple(events))


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _task_id(value: object) -> TaskId:
    if not isinstance(value, str) or not value.strip():
        raise AuditReplayError("audit task ID must be a non-empty string")
    return TaskId(value)


def _utc_timestamp(value: object) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise AuditReplayError("audit timestamp must be timezone-aware")
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise AuditReplayError("audit timestamp must be an ISO-8601 string")
    try:
        parsed_timestamp = datetime.fromisoformat(value)
    except ValueError as error:
        raise AuditReplayError("audit timestamp is not valid ISO-8601") from error
    return _utc_timestamp(parsed_timestamp)


def _normalize_payload(payload: object) -> dict[str, JSONValue]:
    if not isinstance(payload, Mapping):
        raise AuditPayloadError("audit payload must be a mapping with string keys")
    normalized = _normalize_json_value(dict(payload), "payload")
    return cast(dict[str, JSONValue], normalized)


def _normalize_json_value(value: object, location: str) -> JSONValue:
    if value is None or isinstance(value, (str, bool)):
        return cast(JSONValue, value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise AuditPayloadError(f"{location} must not contain a non-finite float")
        return value
    if isinstance(value, Mapping):
        normalized_mapping: dict[str, JSONValue] = {}
        for key, nested_value in value.items():
            if not isinstance(key, str):
                raise AuditPayloadError(f"{location} keys must be strings")
            normalized_mapping[key] = _normalize_json_value(nested_value, f"{location}.{key}")
        return normalized_mapping
    if isinstance(value, (list, tuple)):
        return [
            _normalize_json_value(nested_value, f"{location}[{index}]")
            for index, nested_value in enumerate(value)
        ]
    raise AuditPayloadError(f"{location} contains a value that is not JSON-serializable")


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is not allowed: {value}")
