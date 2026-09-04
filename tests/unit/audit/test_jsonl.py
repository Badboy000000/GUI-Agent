# Copyright (c) 2026, 东篱馆主

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gui_agent.audit import (
    AuditEvent,
    AuditEventKind,
    AuditPayloadError,
    AuditReplayError,
    AuditWriteError,
    JsonlAuditRecorder,
    load_replay,
)
from gui_agent.contracts import TaskId


TASK_ID = TaskId("task-123")
FIRST_TIMESTAMP = datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc)
SECOND_TIMESTAMP = datetime(2026, 9, 4, 8, 1, tzinfo=timezone.utc)


class FixedClock:
    def __init__(self, *timestamps: datetime) -> None:
        self._timestamps = iter(timestamps)

    def __call__(self) -> datetime:
        return next(self._timestamps)


class FlushTrackingFile:
    def __init__(self) -> None:
        self.written = ""
        self.flushed = False

    def __enter__(self) -> "FlushTrackingFile":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def write(self, value: str) -> int:
        self.written += value
        return len(value)

    def flush(self) -> None:
        self.flushed = True


def test_recorder_appends_normalized_jsonl_and_replay_restores_it(tmp_path: Path) -> None:
    audit_path = tmp_path / "runs" / "task-123.jsonl"
    recorder = JsonlAuditRecorder(audit_path, clock=FixedClock(FIRST_TIMESTAMP, SECOND_TIMESTAMP))

    first = recorder.record(
        TASK_ID,
        AuditEventKind.TASK_STARTED,
        {"instruction_id": "request-9", "safe_flags": [True, None]},
    )
    second = recorder.record(
        str(TASK_ID),
        AuditEventKind.DEVICE_HEALTH_CHECKED,
        {"healthy": True, "latency_ms": 12.5},
    )

    assert (first.sequence, second.sequence) == (0, 1)
    lines = audit_path.read_text(encoding="utf-8").splitlines()
    assert [json.loads(line)["sequence"] for line in lines] == [0, 1]
    replay = load_replay(audit_path, task_id=TASK_ID)
    assert replay.task_id == TASK_ID
    assert [(event.kind, dict(event.payload)) for event in replay.events] == [
        (AuditEventKind.TASK_STARTED, {"instruction_id": "request-9", "safe_flags": [True, None]}),
        (AuditEventKind.DEVICE_HEALTH_CHECKED, {"healthy": True, "latency_ms": 12.5}),
    ]
    assert [event.occurred_at for event in replay.events] == [FIRST_TIMESTAMP, SECOND_TIMESTAMP]


def test_recorder_flushes_each_appended_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tracked_file = FlushTrackingFile()

    def fake_open(_: Path, mode: str, **__: object) -> FlushTrackingFile:
        assert mode == "a"
        return tracked_file

    monkeypatch.setattr(Path, "open", fake_open)
    recorder = JsonlAuditRecorder(tmp_path / "task.jsonl", clock=FixedClock(FIRST_TIMESTAMP))

    recorder.record(TASK_ID, AuditEventKind.TASK_STARTED, {})

    assert tracked_file.flushed is True
    assert json.loads(tracked_file.written)["kind"] == "task_started"


def test_recorder_resumes_only_a_valid_existing_trail(tmp_path: Path) -> None:
    audit_path = tmp_path / "task.jsonl"
    JsonlAuditRecorder(audit_path, clock=FixedClock(FIRST_TIMESTAMP)).record(
        TASK_ID, AuditEventKind.TASK_STARTED, {}
    )

    resumed = JsonlAuditRecorder(audit_path, clock=FixedClock(SECOND_TIMESTAMP))
    event = resumed.record(TASK_ID, AuditEventKind.TASK_FINISHED, {"state": "succeeded"})

    assert event.sequence == 1
    assert [event.sequence for event in load_replay(audit_path).events] == [0, 1]


def test_recorder_prevents_interleaving_two_task_ids(tmp_path: Path) -> None:
    recorder = JsonlAuditRecorder(tmp_path / "task.jsonl", clock=FixedClock(FIRST_TIMESTAMP))
    recorder.record(TASK_ID, AuditEventKind.TASK_STARTED, {})

    with pytest.raises(AuditWriteError, match="only one task ID"):
        recorder.record("other-task", AuditEventKind.TASK_FINISHED, {})


@pytest.mark.parametrize(
    "payload",
    [
        {"bad": b"screenshot bytes"},
        {"bad": {1: "non-string key"}},
        {"bad": math.nan},
        ["not a mapping"],
    ],
)
def test_recorder_rejects_non_json_or_non_mapping_payloads(tmp_path: Path, payload: object) -> None:
    recorder = JsonlAuditRecorder(tmp_path / "task.jsonl", clock=FixedClock(FIRST_TIMESTAMP))

    with pytest.raises(AuditPayloadError):
        recorder.record(TASK_ID, AuditEventKind.TASK_STARTED, payload)  # type: ignore[arg-type]

    assert not recorder.path.exists()


def test_recorder_requires_a_declared_event_kind(tmp_path: Path) -> None:
    recorder = JsonlAuditRecorder(tmp_path / "task.jsonl", clock=FixedClock(FIRST_TIMESTAMP))

    with pytest.raises(AuditPayloadError, match="AuditEventKind"):
        recorder.record(TASK_ID, "task_started", {})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("record", "message"),
    [
        ("{bad json}\n", "invalid audit JSON"),
        (
            '{"schema_version":1,"sequence":2,"occurred_at":"2026-09-04T08:00:00+00:00","task_id":"task-123","kind":"task_started","payload":{}}\n',
            "sequence must be contiguous",
        ),
        (
            '{"schema_version":1,"sequence":0,"occurred_at":"2026-09-04T08:00:00+00:00","task_id":"task-123","kind":"unknown","payload":{}}\n',
            "unsupported audit event kind",
        ),
        (
            '{"schema_version":1,"sequence":0,"occurred_at":"2026-09-04T08:00:00","task_id":"task-123","kind":"task_started","payload":{}}\n',
            "timestamp must be timezone-aware",
        ),
    ],
)
def test_replay_rejects_malformed_or_unsafe_records(
    tmp_path: Path, record: str, message: str
) -> None:
    audit_path = tmp_path / "task.jsonl"
    audit_path.write_text(record, encoding="utf-8")

    with pytest.raises(AuditReplayError, match=message):
        load_replay(audit_path)


def test_replay_rejects_task_id_change_and_mismatched_expected_task(tmp_path: Path) -> None:
    audit_path = tmp_path / "task.jsonl"
    recorder = JsonlAuditRecorder(audit_path, clock=FixedClock(FIRST_TIMESTAMP, SECOND_TIMESTAMP))
    recorder.record(TASK_ID, AuditEventKind.TASK_STARTED, {})
    first_line = audit_path.read_text(encoding="utf-8")
    changed_task_line = (
        first_line.replace('"sequence":0', '"sequence":1')
        .replace('"task_id":"task-123"', '"task_id":"other-task"')
    )
    audit_path.write_text(first_line + changed_task_line, encoding="utf-8")

    with pytest.raises(AuditReplayError, match="task ID changes"):
        load_replay(audit_path)

    audit_path.write_text(first_line, encoding="utf-8")
    with pytest.raises(AuditReplayError, match="does not match"):
        load_replay(audit_path, task_id="other-task")


def test_event_rejects_naive_timestamp_and_unknown_schema_fields() -> None:
    with pytest.raises(AuditReplayError, match="timezone-aware"):
        AuditEvent(0, datetime(2026, 9, 4, 8, 0), TASK_ID, AuditEventKind.TASK_STARTED, {})
    with pytest.raises(AuditReplayError, match="fields do not match"):
        AuditEvent.from_json_record(
            {
                "schema_version": 1,
                "sequence": 0,
                "occurred_at": FIRST_TIMESTAMP.isoformat(),
                "task_id": str(TASK_ID),
                "kind": "task_started",
                "payload": {},
                "unexpected": True,
            }
        )


def test_replay_is_data_only_and_exposes_no_execution_api(tmp_path: Path) -> None:
    audit_path = tmp_path / "task.jsonl"
    JsonlAuditRecorder(audit_path, clock=FixedClock(FIRST_TIMESTAMP)).record(
        TASK_ID, AuditEventKind.COMMAND_EXECUTED, {"command": "tap", "validation_id": "v-1"}
    )

    replay = load_replay(audit_path)

    assert [event.kind for event in replay.events] == [AuditEventKind.COMMAND_EXECUTED]
    assert not hasattr(replay, "execute")
    assert not hasattr(replay, "run")
