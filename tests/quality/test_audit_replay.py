# Copyright (c) 2026, 东篱馆主

"""Black-box acceptance coverage for the public local audit boundary."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from gui_agent.audit import AuditEventKind, JsonlAuditRecorder, load_replay


def test_audit_trail_can_be_restored_without_a_device_or_model(tmp_path: Path) -> None:
    audit_path = tmp_path / "p2-run.jsonl"
    recorder = JsonlAuditRecorder(
        audit_path,
        clock=lambda: datetime(2026, 9, 4, 8, 0, tzinfo=timezone.utc),
    )
    recorder.record("p2-run", AuditEventKind.TASK_STARTED, {"instruction_id": "safe-reference"})
    recorder.record("p2-run", AuditEventKind.CONFIRMATION_REQUESTED, {"reason": "human review"})
    recorder.record("p2-run", AuditEventKind.CONFIRMATION_APPROVED, {"approved_by": "operator"})
    recorder.record("p2-run", AuditEventKind.HUMAN_TAKEOVER, {"reason": "operator intervention"})
    recorder.record("p2-run", AuditEventKind.TASK_FINISHED, {"state": "cancelled"})

    replay = load_replay(audit_path, task_id="p2-run")

    assert [event.kind.value for event in replay.events] == [
        "task_started",
        "confirmation_requested",
        "confirmation_approved",
        "human_takeover",
        "task_finished",
    ]
