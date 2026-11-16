"""Tests for app/storage/audit.py."""

from app.storage.audit import AuditLog


def test_rows_come_back_in_order_for_one_recovery_only():
    log = AuditLog()
    log.record("rec-1", "collect_evidence", "collecting_evidence")
    log.record("rec-2", "collect_evidence", "collecting_evidence")
    log.record("rec-1", "diagnose_failure", "diagnosing", "low confidence")
    rows = log.rows("rec-1")
    assert [(r.node, r.status, r.detail) for r in rows] == [
        ("collect_evidence", "collecting_evidence", None),
        ("diagnose_failure", "diagnosing", "low confidence"),
    ]


def test_quotes_in_values_cannot_break_the_query():
    log = AuditLog()
    tricky = "x'); DROP TABLE audit; --"
    log.record(tricky, "node", "status", tricky)
    assert log.rows(tricky)[0].detail == tricky
    assert log.rows("anyone-else") == []


def test_file_database_survives_reopening(tmp_path):
    path = str(tmp_path / "audit.db")
    AuditLog(path).record("rec-1", "collect_evidence", "collecting_evidence")
    assert len(AuditLog(path).rows("rec-1")) == 1
