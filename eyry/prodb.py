"""Read-only helpers for the Eyry Pro SQLite store.

Eyry Pro's agent sandboxes can read the Pro database but must not mutate it.
The ``findings`` and ``scans`` CLI commands use these helpers, always opening
the database in SQLite read-only mode and always filtering by the tenant uid
from ``EYRY_TENANT_UID``.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

DEFAULT_DB = "/var/lib/eyry-pro/eyry_pro.db"

FINDING_FIELDS = (
    "id", "scope_id", "host", "severity", "title", "detail", "vuln_class",
    "class_slug", "status", "status_changed_at", "closed_reason",
    "verified_at", "verify_evidence", "source", "origin_scan_id", "summary",
    "reproduction_steps", "impact", "evidence", "occurrences", "last_seen",
    "created_at",
)

SCAN_FIELDS = (
    "id", "status", "stage", "hosts_found", "hosts_probed",
    "findings_count", "credits_spent", "created_at", "finished_at",
)

_FINDING_DEFAULTS = {
    "scope_id": None,
    "host": "",
    "severity": "info",
    "title": "",
    "detail": "",
    "vuln_class": "",
    "class_slug": "",
    "class_title": "",
    "status": "open",
    "status_changed_at": 0.0,
    "closed_reason": "",
    "verified_at": 0.0,
    "verified": False,
    "verify_evidence": "",
    "source": "",
    "origin_scan_id": None,
    "summary": None,
    "reproduction_steps": [],
    "reproduction_steps_raw": None,
    "impact": None,
    "remediation": "",
    "evidence": [],
    "evidence_raw": None,
    "detail_sections": {},
    "occurrences": 0,
    "first_seen": 0.0,
    "last_seen": 0.0,
    "updated_at": 0.0,
    "created_at": 0.0,
    "scope": "",
    "scope_pattern": "",
}


class ProDBError(Exception):
    """A user-facing problem opening or reading the Pro database."""


def tenant_uid() -> str:
    """Return the required tenant uid, or raise a one-line CLI error."""
    uid = (os.environ.get("EYRY_TENANT_UID") or "").strip()
    if not uid:
        raise ProDBError(
            "EYRY_TENANT_UID is required (set it to this tenant's uid)")
    return uid


def database_path() -> Path:
    raw = os.environ.get("EYRY_PRO_DB") or DEFAULT_DB
    return Path(os.path.expanduser(raw)).resolve()


@contextmanager
def connect():
    """Open the Pro database read-only, or raise ProDBError with a clear line."""
    path = database_path()
    if not path.exists():
        raise ProDBError(
            f"Eyry Pro database not found: {path} (set EYRY_PRO_DB)")
    if not path.is_file():
        raise ProDBError(f"Eyry Pro database is not a file: {path}")
    try:
        readable_bits = path.stat().st_mode & 0o444
    except OSError as exc:
        raise ProDBError(f"cannot read Eyry Pro database {path}: {exc}") from exc
    if not readable_bits or not os.access(path, os.R_OK):
        raise ProDBError(f"Eyry Pro database is not readable: {path}")

    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise ProDBError(f"cannot open Eyry Pro database {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return []


def _json_array(raw) -> list:
    """Parse a stored JSON array; invalid or absent values become []."""
    if isinstance(raw, (list, tuple)):
        return list(raw)
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


_SECTION_RE = re.compile(
    r"^\s*(summary|repro(?:duction)?(?:\s+steps)?|impact|remediation|"
    r"evidence)\s*:\s*(.*)$", re.IGNORECASE)
_SECTION_KEYS = {
    "summary": "summary",
    "repro": "reproduction_steps",
    "reproduction": "reproduction_steps",
    "reproduction steps": "reproduction_steps",
    "impact": "impact",
    "remediation": "remediation",
    "evidence": "evidence",
}


def _detail_sections(detail) -> dict:
    text = "" if detail is None else str(detail)
    if not text.strip():
        return {}
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        match = _SECTION_RE.match(line)
        if match:
            key = " ".join(match.group(1).lower().split())
            current = _SECTION_KEYS.get(
                key, "reproduction_steps" if key.startswith("repro") else key)
            sections.setdefault(current, [])
            remainder = match.group(2).strip()
            if remainder:
                sections[current].append(remainder)
            continue
        if current is not None:
            sections[current].append(line)
    parsed: dict = {}
    for key, lines in sections.items():
        value = "\n".join(lines).strip()
        if not value:
            continue
        if key == "reproduction_steps":
            parsed[key] = [line.strip() for line in value.splitlines()
                           if line.strip()]
        else:
            parsed[key] = value
    return parsed


def _class_meta(conn: sqlite3.Connection, slug: str) -> tuple[str, str]:
    if not slug:
        return "", ""
    try:
        row = conn.execute(
            "SELECT title, remediation FROM vuln_classes WHERE slug=?",
            (slug,)).fetchone()
    except sqlite3.Error:
        return "", ""
    if not row:
        return "", ""
    return str(row["title"] or ""), str(row["remediation"] or "")


def _finding_record(row: sqlite3.Row,
                    conn: sqlite3.Connection | None = None) -> dict:
    record = dict(row)
    for key, default in _FINDING_DEFAULTS.items():
        record.setdefault(key, default)
    raw_repro = record.get("reproduction_steps")
    raw_evidence = record.get("evidence")
    sections = _detail_sections(record.get("detail"))
    record["detail_sections"] = sections
    record["reproduction_steps_raw"] = raw_repro
    record["evidence_raw"] = raw_evidence
    record["reproduction_steps"] = _json_array(raw_repro)
    if not record["reproduction_steps"]:
        record["reproduction_steps"] = list(
            sections.get("reproduction_steps") or [])
    record["evidence"] = _json_array(raw_evidence)
    if not record["evidence"] and sections.get("evidence"):
        record["evidence"] = [{
            "kind": "detail", "label": "Evidence",
            "content": sections["evidence"]}]
    if not record.get("summary"):
        record["summary"] = sections.get("summary") or ""
    if not record.get("impact"):
        record["impact"] = sections.get("impact") or ""
    class_title, class_remediation = _class_meta(
        conn, record.get("class_slug") or "")
    record["class_title"] = class_title
    record["remediation"] = (
        sections.get("remediation") or class_remediation or "")
    record["verified"] = bool(float(record.get("verified_at") or 0) > 0)
    record["first_seen"] = record.get("created_at") or 0.0
    record["last_seen"] = record.get("last_seen") or record.get("created_at")
    record["updated_at"] = record["last_seen"] or record.get(
        "status_changed_at") or 0.0
    record["scope"] = record.get("scope_pattern") or ""
    return record


def _finding_select(conn: sqlite3.Connection) -> tuple[str, str]:
    columns = set(_columns(conn, "findings"))
    if not columns:
        raise ProDBError("Eyry Pro database has no findings table")
    selected = [c for c in FINDING_FIELDS if c in columns]
    if "id" not in selected:
        raise ProDBError("Eyry Pro findings table has no id column")
    order = "f.created_at DESC, f.id DESC" if "created_at" in columns else "f.id DESC"
    return ", ".join(f"f.{c}" for c in selected), order


def _scope_columns(conn: sqlite3.Connection) -> set[str]:
    columns = set(_columns(conn, "scopes"))
    if not columns:
        raise ProDBError("Eyry Pro database has no scopes table")
    return columns


def list_findings(conn: sqlite3.Connection, uid: str,
                  scope_id: int | None = None,
                  status: str | None = None) -> list[dict]:
    """Tenant-scoped findings, newest first."""
    selected, order = _finding_select(conn)
    where = "f.firebase_uid=?"
    params: list = [uid]
    if scope_id is not None:
        where += " AND f.scope_id=?"
        params.append(scope_id)
    status = (status or "").strip().lower() or None
    if status:
        where += " AND f.status=?"
        params.append(status)
    rows = conn.execute(
        f"SELECT {selected}, s.pattern AS scope_pattern"
        " FROM findings f"
        " LEFT JOIN scopes s ON s.id=f.scope_id"
        "  AND s.firebase_uid=f.firebase_uid"
        f" WHERE {where} ORDER BY {order}",
        params,
    ).fetchall()
    return [_finding_record(row, conn) for row in rows]


def get_finding(conn: sqlite3.Connection, uid: str,
                finding_id: int) -> dict | None:
    """One tenant-scoped finding with its report fields, or None."""
    selected, _order = _finding_select(conn)
    row = conn.execute(
        f"SELECT {selected}, s.pattern AS scope_pattern"
        " FROM findings f"
        " LEFT JOIN scopes s ON s.id=f.scope_id"
        "  AND s.firebase_uid=f.firebase_uid"
        " WHERE f.firebase_uid=? AND f.id=?",
        (uid, finding_id),
    ).fetchone()
    return _finding_record(row, conn) if row else None


def _count(conn: sqlite3.Connection, sql: str, params: tuple) -> int:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return 0
    return int(row[0]) if row else 0


def _live_phase(status, stage) -> str | None:
    raw_status = str(status or "").strip().lower()
    normalized = {"queued": "pending", "done": "complete"}.get(
        raw_status, raw_status)
    live_stage = str(stage or "").strip().lower()
    if normalized in ("pending", "running") and live_stage:
        return live_stage
    return normalized or live_stage or None


def _as_of(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def scan_status(conn: sqlite3.Connection, uid: str,
                scope_id: int | None = None,
                now: float | None = None) -> dict:
    """Latest scan per tenant scope, with live counts from source tables."""
    now = time.time() if now is None else float(now)
    _scope_columns(conn)
    where = "firebase_uid=?"
    params: list = [uid]
    if scope_id is not None:
        where += " AND id=?"
        params.append(scope_id)
    scope_rows = conn.execute(
        "SELECT id, pattern FROM scopes"
        f" WHERE {where} ORDER BY id",
        params,
    ).fetchall()

    scan_columns = set(_columns(conn, "scans"))
    scan_selected = [c for c in SCAN_FIELDS if c in scan_columns]
    finding_columns = set(_columns(conn, "findings"))
    host_columns = set(_columns(conn, "hosts"))

    out = []
    for scope in scope_rows:
        sid = int(scope["id"])
        scan = None
        if scan_selected and "id" in scan_selected:
            scan_row = conn.execute(
                f"SELECT {', '.join(scan_selected)} FROM scans"
                " WHERE firebase_uid=? AND scope_id=?"
                " ORDER BY id DESC LIMIT 1",
                (uid, sid),
            ).fetchone()
            scan = dict(scan_row) if scan_row else None

        findings_count = 0
        if "firebase_uid" in finding_columns and "scope_id" in finding_columns:
            findings_count = _count(
                conn,
                "SELECT COUNT(*) FROM findings WHERE firebase_uid=? AND scope_id=?",
                (uid, sid),
            )

        hosts_total = 0
        resolved_hosts = 0
        reviewed_hosts = 0
        if "firebase_uid" in host_columns and "scope_id" in host_columns:
            hosts_total = _count(
                conn,
                "SELECT COUNT(*) FROM hosts WHERE firebase_uid=? AND scope_id=?",
                (uid, sid),
            )
            if "resolved" in host_columns:
                resolved_hosts = _count(
                    conn,
                    "SELECT COUNT(*) FROM hosts WHERE firebase_uid=? AND scope_id=?"
                    " AND COALESCE(resolved, 1) <> 0",
                    (uid, sid),
                )
            else:
                resolved_hosts = hosts_total
            if "last_probed_at" in host_columns:
                reviewed_hosts = _count(
                    conn,
                    "SELECT COUNT(*) FROM hosts WHERE firebase_uid=? AND scope_id=?"
                    " AND last_probed_at IS NOT NULL",
                    (uid, sid),
                )

        status = scan.get("status") if scan else None
        stage = scan.get("stage") if scan else None
        phase = _live_phase(status, stage) if scan else None
        record = {
            "scope_id": sid,
            "scope_pattern": scope["pattern"],
            "scan_id": scan.get("id") if scan else None,
            "status": status,
            "stage": stage or "",
            "phase": phase,
            "live_phase": phase,
            "findings_count": findings_count,
            "cached_findings_count": (
                scan.get("findings_count") if scan else None),
            "hosts_total": hosts_total,
            "resolved_hosts": resolved_hosts,
            "reviewed_hosts": reviewed_hosts,
            "hosts_resolved": resolved_hosts,
            "hosts_reviewed": reviewed_hosts,
            "hosts_found": scan.get("hosts_found") if scan else None,
            "hosts_probed": scan.get("hosts_probed") if scan else None,
            "credits_spent": scan.get("credits_spent") if scan else None,
            "scan_created_at": scan.get("created_at") if scan else None,
            "finished_at": scan.get("finished_at") if scan else None,
            "as_of": now,
            "as_of_iso": _as_of(now),
        }
        out.append(record)

    return {
        "as_of": now,
        "as_of_iso": _as_of(now),
        "scopes": out,
    }
