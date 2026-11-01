"""Read paths for the Eyry Pro findings and scan-status commands."""

from __future__ import annotations

import json
import sqlite3

from eyry.cli import main

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"

SCHEMA = """
CREATE TABLE scopes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firebase_uid TEXT NOT NULL,
    pattern TEXT NOT NULL,
    created_at REAL NOT NULL,
    scan_cadence TEXT NOT NULL DEFAULT 'off',
    mode TEXT NOT NULL DEFAULT 'ownership',
    verification_token TEXT NOT NULL DEFAULT '',
    verified_owner TEXT NOT NULL DEFAULT '',
    grandfathered INTEGER NOT NULL DEFAULT 0,
    ai_scan_enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE(firebase_uid, pattern)
);
CREATE TABLE findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firebase_uid TEXT NOT NULL,
    scope_id INTEGER NOT NULL,
    host TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    vuln_class TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    status_changed_at REAL NOT NULL DEFAULT 0,
    closed_reason TEXT NOT NULL DEFAULT '',
    verified_at REAL NOT NULL DEFAULT 0,
    verify_evidence TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    origin_scan_id INTEGER,
    summary TEXT,
    reproduction_steps TEXT,
    impact TEXT,
    evidence TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firebase_uid TEXT NOT NULL,
    scope_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    stage TEXT NOT NULL DEFAULT '',
    hosts_found INTEGER NOT NULL DEFAULT 0,
    hosts_probed INTEGER NOT NULL DEFAULT 0,
    findings_count INTEGER NOT NULL DEFAULT 0,
    credits_spent INTEGER NOT NULL DEFAULT 0,
    errors TEXT NOT NULL DEFAULT '[]',
    diff_json TEXT NOT NULL DEFAULT '',
    artifacts TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    finished_at REAL
);
CREATE TABLE hosts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firebase_uid TEXT NOT NULL,
    scope_id INTEGER NOT NULL,
    host TEXT NOT NULL,
    first_seen REAL NOT NULL,
    last_probed_at REAL,
    probe_data TEXT NOT NULL DEFAULT '',
    resolved INTEGER,
    last_resolved_at REAL,
    UNIQUE(firebase_uid, scope_id, host)
);
"""


def _db(tmp_path):
    path = tmp_path / "eyry_pro.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT INTO scopes (id, firebase_uid, pattern, created_at)"
        " VALUES (?, ?, ?, ?)",
        [
            (1, TENANT_A, "*.a.example", 100.0),
            (2, TENANT_B, "*.b.example", 100.0),
            (3, TENANT_A, "*.a2.example", 100.0),
        ],
    )
    conn.executemany(
        "INSERT INTO findings (id, firebase_uid, scope_id, host, severity,"
        " title, detail, vuln_class, status, summary, reproduction_steps,"
        " impact, evidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,"
        " ?, ?, ?, ?, ?)",
        [
            (1, TENANT_A, 1, "one.a.example", "high", "A open one",
             "detail one", "xss", "open", "summary one",
             '["step one", "step two"]', "impact one",
             '[{"kind": "request_response", "content": "proof"}]', 110.0),
            (2, TENANT_A, 1, "two.a.example", "medium", "A closed",
             "", "", "closed", None, None, None, None, 120.0),
            (3, TENANT_A, 1, "three.a.example", "low", "A informative",
             "", "", "informative", None, None, None, None, 130.0),
            (4, TENANT_A, 3, "one.a2.example", "critical", "A second scope",
             "", "", "open", None, None, None, None, 140.0),
            (5, TENANT_B, 2, "one.b.example", "critical", "B secret",
             "", "", "open", None, None, None, None, 150.0),
            (6, TENANT_B, 2, "two.b.example", "high", "B secret two",
             "", "", "open", None, None, None, None, 160.0),
        ],
    )
    conn.executemany(
        "INSERT INTO scans (id, firebase_uid, scope_id, status, stage,"
        " hosts_found, hosts_probed, findings_count, created_at, finished_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (9, TENANT_A, 1, "complete", "done", 4, 2, 99, 900.0, 901.0),
            (10, TENANT_A, 1, "running", "probing", 6, 2, 0, 1000.0, None),
            (11, TENANT_A, 3, "complete", "done", 1, 1, 42, 1010.0, 1011.0),
            (12, TENANT_B, 2, "complete", "done", 2, 2, 0, 1020.0, 1021.0),
        ],
    )
    conn.executemany(
        "INSERT INTO hosts (firebase_uid, scope_id, host, first_seen,"
        " last_probed_at, resolved) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (TENANT_A, 1, "one.a.example", 100.0, 200.0, 1),
            (TENANT_A, 1, "two.a.example", 100.0, None, 1),
            (TENANT_A, 1, "three.a.example", 100.0, None, 0),
            (TENANT_A, 1, "four.a.example", 100.0, 300.0, None),
            (TENANT_B, 2, "one.b.example", 100.0, 200.0, 1),
            (TENANT_B, 2, "two.b.example", 100.0, 200.0, 1),
        ],
    )
    conn.commit()
    conn.close()
    return path


def _run(monkeypatch, capsys, path, uid, argv):
    monkeypatch.setenv("EYRY_PRO_DB", str(path))
    if uid is None:
        monkeypatch.delenv("EYRY_TENANT_UID", raising=False)
    else:
        monkeypatch.setenv("EYRY_TENANT_UID", uid)
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_findings_list_is_tenant_scoped(monkeypatch, capsys, tmp_path):
    path = _db(tmp_path)
    code, out, err = _run(
        monkeypatch, capsys, path, TENANT_A,
        ["findings", "list", "--json"])
    assert code == 0, err
    data = json.loads(out)
    assert data["count"] == 4
    titles = {row["title"] for row in data["findings"]}
    assert "A open one" in titles
    assert "A second scope" in titles
    assert "B secret" not in titles
    assert "B secret two" not in titles
    assert all(row["scope_id"] in (1, 3) for row in data["findings"])


def test_other_tenant_scope_filter_returns_nothing(monkeypatch, capsys,
                                                   tmp_path):
    path = _db(tmp_path)
    code, out, err = _run(
        monkeypatch, capsys, path, TENANT_A,
        ["findings", "list", "--scope", "2", "--json"])
    assert code == 0, err
    assert json.loads(out)["findings"] == []


def test_findings_show_enforces_tenant_ownership(monkeypatch, capsys,
                                                 tmp_path):
    path = _db(tmp_path)
    code, out, err = _run(
        monkeypatch, capsys, path, TENANT_A,
        ["findings", "show", "5", "--json"])
    assert code == 1
    assert "not found for this tenant" in err
    assert "Traceback" not in err
    assert out == ""


def test_findings_show_includes_report_sections(monkeypatch, capsys,
                                                tmp_path):
    path = _db(tmp_path)
    code, out, err = _run(
        monkeypatch, capsys, path, TENANT_A,
        ["findings", "show", "1", "--json"])
    assert code == 0, err
    finding = json.loads(out)
    assert finding["title"] == "A open one"
    assert finding["summary"] == "summary one"
    assert finding["reproduction_steps"] == ["step one", "step two"]
    assert finding["impact"] == "impact one"
    assert finding["evidence"][0]["content"] == "proof"


def test_findings_status_filter(monkeypatch, capsys, tmp_path):
    path = _db(tmp_path)
    code, out, err = _run(
        monkeypatch, capsys, path, TENANT_A,
        ["findings", "list", "--status", "closed", "--json"])
    assert code == 0, err
    data = json.loads(out)
    assert data["count"] == 1
    assert data["findings"][0]["title"] == "A closed"


def test_scan_status_uses_live_findings_count(monkeypatch, capsys, tmp_path):
    path = _db(tmp_path)
    code, out, err = _run(
        monkeypatch, capsys, path, TENANT_A,
        ["scans", "status", "--json"])
    assert code == 0, err
    data = json.loads(out)
    assert "as_of" in data
    assert data["as_of_iso"].endswith("Z")
    by_scope = {row["scope_id"]: row for row in data["scopes"]}
    assert set(by_scope) == {1, 3}
    assert 2 not in by_scope  # the other tenant's scope

    latest = by_scope[1]
    assert latest["scan_id"] == 10
    assert latest["status"] == "running"
    assert latest["phase"] == "probing"
    assert latest["cached_findings_count"] == 0
    assert latest["findings_count"] == 3
    assert latest["resolved_hosts"] == 3
    assert latest["reviewed_hosts"] == 2

    second = by_scope[3]
    assert second["findings_count"] == 1
    assert second["cached_findings_count"] == 42


def test_scan_status_other_tenant(monkeypatch, capsys, tmp_path):
    path = _db(tmp_path)
    code, out, err = _run(
        monkeypatch, capsys, path, TENANT_B,
        ["scans", "status", "--json"])
    assert code == 0, err
    data = json.loads(out)
    assert [row["scope_id"] for row in data["scopes"]] == [2]
    assert data["scopes"][0]["findings_count"] == 2


def test_tenant_uid_is_required(monkeypatch, capsys, tmp_path):
    path = _db(tmp_path)
    code, out, err = _run(
        monkeypatch, capsys, path, None,
        ["findings", "list"])
    assert code == 1
    assert "EYRY_TENANT_UID is required" in err
    assert "Traceback" not in err
    assert out == ""


def test_missing_database_is_a_one_line_error(monkeypatch, capsys, tmp_path):
    path = tmp_path / "absent.db"
    code, out, err = _run(
        monkeypatch, capsys, path, TENANT_A,
        ["scans", "status"])
    assert code == 1
    assert "database not found" in err
    assert "Traceback" not in err
    assert out == ""


def test_unreadable_database_is_a_one_line_error(monkeypatch, capsys,
                                                tmp_path):
    path = _db(tmp_path)
    path.chmod(0)
    try:
        code, out, err = _run(
            monkeypatch, capsys, path, TENANT_A,
            ["findings", "list"])
    finally:
        path.chmod(0o600)
    assert code == 1
    assert "not readable" in err
    assert "Traceback" not in err
    assert out == ""


def test_human_finding_output_has_sections(monkeypatch, capsys, tmp_path):
    path = _db(tmp_path)
    code, out, err = _run(
        monkeypatch, capsys, path, TENANT_A,
        ["findings", "show", "1"])
    assert code == 0, err
    assert "Summary" in out
    assert "Reproduction steps" in out
    assert "step one" in out
