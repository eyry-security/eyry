"""The eyry command-line interface.

Commands:
  doctor   check that the suite's tools and services are available
  init     create the Rutt schema
  up       run the whole pipeline for a scope (discover -> queue -> probe -> store)
  status   Redis + queue depths + Rutt row counts
  version  versions of eyry and every component

Plus one hidden helper used inside `up`:
  pipe-hosts   read Foretop JSONL on stdin and LPUSH each .host to a Redis queue
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlparse

from . import __version__, prodb
from .config import Config
from .supervisor import Stage, Supervisor

COMPONENTS = ["foretop", "purser", "vedette", "rutt"]
OPTIONAL_COMPONENTS = ["aplomado"]  # checked by doctor, skipped if absent in up


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _redis_endpoint(url: str) -> tuple[str, int]:
    u = urlparse(url)
    return (u.hostname or "127.0.0.1", u.port or 6379)


def _redis_ok(url: str, timeout: float = 2.0) -> bool:
    host, port = _redis_endpoint(url)
    try:
        with socket.create_connection((host, port), timeout=timeout) as s:
            s.sendall(b"PING\r\n")
            return b"PONG" in s.recv(64)
    except OSError:
        return False


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _tool_version(tool: str) -> str | None:
    if not shutil.which(tool):
        return None
    r = _run([tool, "--version"])
    return (r.stdout or r.stderr).strip() or "(present)"


def _fmt_cell(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ",".join(map(str, value))
    return str(value)


def _print_table(rows: list[dict], columns: list[str], empty: str) -> None:
    if not rows:
        print(empty)
        return
    widths = {c: len(c) for c in columns}
    table = []
    for row in rows:
        formatted = {c: _fmt_cell(row.get(c)) for c in columns}
        for c in columns:
            widths[c] = min(max(widths[c], len(formatted[c])), 48)
        table.append(formatted)
    print("  ".join(c.ljust(widths[c]) for c in columns))
    print("  ".join("-" * widths[c] for c in columns))
    for row in table:
        print("  ".join(row[c][:widths[c]].ljust(widths[c]) for c in columns))
    print(f"\n{len(rows)} row(s)")


def _fmt_time(value) -> str:
    try:
        stamp = float(value)
    except (TypeError, ValueError):
        return "-"
    if stamp <= 0:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(stamp))


def _print_finding(finding: dict) -> None:
    print(f"finding #{finding.get('id')}  "
          f"[{finding.get('severity') or 'info'}] {finding.get('status') or 'open'}")
    print(f"  title:     {finding.get('title') or '-'}")
    print(f"  host:      {finding.get('host') or '-'}")
    print(f"  scope:     {finding.get('scope_pattern') or finding.get('scope_id') or '-'}")
    print(f"  class:     {finding.get('vuln_class') or '-'}")
    print(f"  source:    {finding.get('source') or '-'}")
    print(f"  created:   {_fmt_time(finding.get('created_at'))}")
    if finding.get("closed_reason"):
        print(f"  closed:    {finding.get('closed_reason')}")

    summary = finding.get("summary") or finding.get("detail") or ""
    print("\nSummary\n-------")
    print(str(summary).strip() or "Not recorded.")

    print("\nReproduction steps\n------------------")
    steps = finding.get("reproduction_steps") or []
    if steps:
        for index, step in enumerate(steps, 1):
            print(f"{index}. {step}")
    else:
        print("Not recorded.")

    print("\nImpact\n------")
    print(str(finding.get("impact") or "").strip() or "Not recorded.")

    print("\nEvidence\n--------")
    evidence = finding.get("evidence") or []
    if not evidence:
        print("Not recorded.")
    for item in evidence:
        if isinstance(item, dict):
            print(json.dumps(item, indent=2, ensure_ascii=False))
        else:
            print(str(item))

    if finding.get("verify_evidence"):
        print("\nVerification evidence\n---------------------")
        print(finding["verify_evidence"])


def _pro_command_error(exc: Exception) -> int:
    print(f"eyry: {exc}", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_version(cfg: Config, args) -> int:
    print(f"eyry {__version__}")
    for tool in COMPONENTS + OPTIONAL_COMPONENTS:
        v = _tool_version(tool)
        print(f"  {tool:12} {v if v else '- not found on PATH'}")
    return 0


def cmd_doctor(cfg: Config, args) -> int:
    ok = True
    print("tools (required):")
    for tool in COMPONENTS:
        v = _tool_version(tool)
        mark = "ok" if v else "MISSING"
        if not v:
            ok = False
        print(f"  [{mark:>7}] {tool:12} {v or 'not on PATH'}")

    print("tools (optional):")
    for tool in OPTIONAL_COMPONENTS:
        v = _tool_version(tool)
        mark = "ok" if v else "absent"
        print(f"  [{mark:>7}] {tool:12} {v or 'not on PATH (AI review stage will be skipped)'}")

    print("services:")
    r_ok = _redis_ok(cfg.redis_url)
    ok = ok and r_ok
    print(f"  [{'ok' if r_ok else 'MISSING':>7}] redis    {cfg.redis_url}")

    pg = _run(["rutt", "stats", "--dsn", cfg.dsn, "--json"]) if shutil.which("rutt") else None
    pg_ok = bool(pg and pg.returncode == 0)
    ok = ok and pg_ok
    detail = cfg.dsn if pg_ok else (pg.stderr.strip() if pg else "rutt not installed")
    print(f"  [{'ok' if pg_ok else 'MISSING':>7}] postgres {detail}")

    print("\nready" if ok else "\nnot ready — fix the MISSING items above")
    return 0 if ok else 1


def cmd_init(cfg: Config, args) -> int:
    if not shutil.which("rutt"):
        print("eyry: rutt is not installed", file=sys.stderr)
        return 1
    r = _run(["rutt", "init", "--dsn", cfg.dsn])
    sys.stdout.write(r.stdout)
    sys.stderr.write(r.stderr)
    return r.returncode


def cmd_status(cfg: Config, args) -> int:
    print(f"redis    {cfg.redis_url}   {'up' if _redis_ok(cfg.redis_url) else 'DOWN'}")

    if shutil.which("purser"):
        r = _run(["purser", "stats", "--redis", cfg.redis_url])
        print(f"queue    {r.stdout.strip() or r.stderr.strip()}")
    else:
        print("queue    (purser not installed)")

    if shutil.which("rutt"):
        r = _run(["rutt", "stats", "--dsn", cfg.dsn])
        print(f"store    {r.stdout.strip() or r.stderr.strip()}")
    else:
        print("store    (rutt not installed)")
    return 0


def cmd_watch(cfg: Config, args) -> int:
    """A live, refreshing dashboard: queue depths, store counts, recent scans."""
    import time
    try:
        while True:
            sys.stdout.write("\033[2J\033[H")  # clear screen, home cursor
            up = "up" if _redis_ok(cfg.redis_url) else "DOWN"
            print(f"eyry \u2014 {time.strftime('%Y-%m-%d %H:%M:%S')}    redis: {up}")
            print("=" * 72)
            if shutil.which("purser"):
                r = _run(["purser", "stats", "--redis", cfg.redis_url])
                print("queue  " + (r.stdout.strip() or r.stderr.strip()))
            if shutil.which("rutt"):
                r = _run(["rutt", "stats", "--dsn", cfg.dsn])
                print("store  " + (r.stdout.strip() or r.stderr.strip()))
                print("\nrecent activity (scan log):")
                r = _run(["rutt", "scans", "--limit", "12", "--dsn", cfg.dsn])
                print(r.stdout.rstrip() or r.stderr.strip())
            print(f"\n(refreshing every {args.interval:g}s \u2014 ctrl-c to stop)")
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0


def cmd_pipe_hosts(cfg: Config, args) -> int:
    """Hidden helper: read Foretop JSONL on stdin, LPUSH each .host to a queue."""
    import redis  # local import so `eyry` w/o redis still loads for --help

    client = redis.from_url(args.redis, decode_responses=True)
    n = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            host = json.loads(line).get("host")
        except json.JSONDecodeError:
            continue
        if host:
            client.lpush(args.queue, host)
            n += 1
    print(f"[eyry:pipe-hosts] enqueued {n} host(s) to {args.queue}", file=sys.stderr)
    return 0


def _pipeline_stages(cfg: Config, scopes: list[str], scope_file: str | None,
                     *, review: bool = True, event_sink: str | None = None) -> list[Stage]:
    py = sys.executable
    if scope_file:
        scope_args = f"--scope-file {scope_file!r}"
    else:
        scope_args = " ".join(f"--scope {s!r}" for s in scopes)
    # discover: Foretop -> (record discovery in Rutt) + (enqueue hosts for probing)
    discover = (
        f"foretop --json {scope_args} "
        f"| tee >(rutt ingest foretop - --dsn {cfg.dsn!r}) "
        f"| eyry pipe-hosts --redis {cfg.redis_url!r} --queue {cfg.ingest_queue!r}"
    )
    # queue: dedup/prioritize, then feed the prober's list
    ingest = f"purser ingest --from {cfg.ingest_queue!r} --tier {cfg.tier} --redis {cfg.redis_url!r}"
    feed = f"purser feed --to {cfg.probe_queue!r} --redis {cfg.redis_url!r}"
    reap = f"purser reap --redis {cfg.redis_url!r}"
    # probe -> store: Vedette reads the list and writes JSONL to stdout (its
    # default), which streams straight into Rutt.
    probe = (
        f"vedette --redis {cfg.redis_url!r} --queue {cfg.probe_queue!r} "
        f"| tee >(rutt ingest vedette - --dsn {cfg.dsn!r})"
    )
    stages = [
        Stage("discover", discover),
        Stage("queue", ingest),
        Stage("feed", feed),
        Stage("reap", reap),
    ]

    if review and shutil.which("aplomado"):
        # review: Aplomado reads Vedette JSONL from the probe stage's stdout,
        # scans each target, writes findings to Rutt, and emits events.
        review_cmd = f"aplomado scan --rutt-dsn {cfg.dsn!r}"
        if event_sink:
            review_cmd += f" --event-sink {event_sink!r}"
        # probe stage tees to both rutt ingest AND the aplomado review stage
        probe_with_review = f"{probe} | {review_cmd}"
        stages.append(Stage("probe+review", probe_with_review))
    else:
        # No aplomado: probe pipes directly into rutt ingest (original behavior).
        probe_store_only = (
            f"vedette --redis {cfg.redis_url!r} --queue {cfg.probe_queue!r} "
            f"| rutt ingest vedette - --dsn {cfg.dsn!r}"
        )
        stages.append(Stage("probe", probe_store_only))
        if review and not shutil.which("aplomado"):
            print("[eyry] aplomado not found on PATH — AI review stage skipped",
                  file=sys.stderr)

    return stages


def cmd_up(cfg: Config, args) -> int:
    if not args.scope and not args.scope_file:
        print("eyry: give --scope (repeatable) or --scope-file", file=sys.stderr)
        return 2
    stages = _pipeline_stages(cfg, args.scope, args.scope_file,
                              review=not args.no_review,
                              event_sink=args.event_sink)
    scope_desc = args.scope_file if args.scope_file else ", ".join(args.scope)
    if args.dry_run:
        print(f"# eyry pipeline for scope {scope_desc!r}")
        print(f"# redis={cfg.redis_url}  dsn={cfg.dsn}  tier={cfg.tier}\n")
        for st in stages:
            print(f"## {st.name}\n{st.command}\n")
        return 0

    missing = [t for t in COMPONENTS if not shutil.which(t)]
    if missing:
        print(f"eyry: missing tools: {', '.join(missing)} (run `eyry doctor`)", file=sys.stderr)
        return 1
    if not _redis_ok(cfg.redis_url):
        print(f"eyry: redis not reachable at {cfg.redis_url}", file=sys.stderr)
        return 1

    print(f"[eyry] pipeline up for scope {scope_desc!r} — ctrl-c to stop", file=sys.stderr)
    return Supervisor(stages).run()


def cmd_findings(cfg: Config, args) -> int:
    """Read tenant findings from the Eyry Pro SQLite store."""
    try:
        uid = prodb.tenant_uid()
        with prodb.connect() as conn:
            if args.findings_cmd == "list":
                findings = prodb.list_findings(
                    conn, uid, scope_id=args.scope, status=args.status)
                if args.json:
                    print(json.dumps(
                        {"findings": findings, "count": len(findings)},
                        ensure_ascii=False))
                else:
                    rows = [{
                        "id": f.get("id"),
                        "severity": f.get("severity"),
                        "status": f.get("status"),
                        "host": f.get("host"),
                        "title": f.get("title"),
                        "scope": f.get("scope_pattern") or f.get("scope_id"),
                        "created": _fmt_time(f.get("created_at")),
                    } for f in findings]
                    _print_table(
                        rows,
                        ["id", "severity", "status", "host", "title", "scope",
                         "created"],
                        "(no findings)",
                    )
                return 0

            finding = prodb.get_finding(conn, uid, args.finding_id)
            if finding is None:
                print(f"eyry: finding {args.finding_id} not found for this tenant",
                      file=sys.stderr)
                return 1
            if args.json:
                print(json.dumps(finding, ensure_ascii=False))
            else:
                _print_finding(finding)
            return 0
    except (prodb.ProDBError, sqlite3.Error) as exc:
        return _pro_command_error(exc)


def cmd_scans(cfg: Config, args) -> int:
    """Show the latest tenant scan per scope from the Eyry Pro store."""
    try:
        uid = prodb.tenant_uid()
        with prodb.connect() as conn:
            result = prodb.scan_status(conn, uid, scope_id=args.scope)
        if args.json:
            print(json.dumps(result, ensure_ascii=False))
            return 0
        rows = [{
            "scope": scan.get("scope_pattern") or scan.get("scope_id"),
            "scan": scan.get("scan_id") or "-",
            "status": scan.get("status") or "-",
            "phase": scan.get("live_phase") or "-",
            "findings": scan.get("findings_count", 0),
            "resolved": scan.get("resolved_hosts", 0),
            "reviewed": scan.get("reviewed_hosts", 0),
            "updated": _fmt_time(scan.get("finished_at")
                                 or scan.get("scan_created_at")),
        } for scan in result["scopes"]]
        _print_table(
            rows,
            ["scope", "scan", "status", "phase", "findings", "resolved",
             "reviewed", "updated"],
            "(no scopes)",
        )
        print(f"\nas of {result['as_of_iso']}")
        return 0
    except (prodb.ProDBError, sqlite3.Error) as exc:
        return _pro_command_error(exc)


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="eyry",
        description="Run the Eyry recon suite together: discover, queue, probe, store.",
    )
    p.add_argument("--version", action="version", version=f"eyry {__version__}")

    def common(sp):
        sp.add_argument("--redis", default=None, help="Redis URL (env EYRY_REDIS)")
        sp.add_argument("--dsn", default=None, help="Postgres DSN (env RUTT_DSN)")

    sub = p.add_subparsers(dest="cmd", required=True)

    common(sub.add_parser("doctor", help="check tools and services"))
    common(sub.add_parser("init", help="create the Rutt schema"))
    common(sub.add_parser("status", help="redis + queue + store status"))
    sub.add_parser("version", help="versions of eyry and components")

    w = sub.add_parser("watch", help="live dashboard: queue depths, store counts, recent scans")
    common(w)
    w.add_argument("--interval", type=float, default=2.0, help="refresh seconds (default 2)")

    findings = sub.add_parser(
        "findings", help="read findings from the Eyry Pro database")
    findings_sub = findings.add_subparsers(dest="findings_cmd", required=True)

    fl = findings_sub.add_parser("list", help="list this tenant's findings")
    fl.add_argument("--scope", type=int, metavar="ID",
                    help="only findings for this scope id")
    fl.add_argument("--status", metavar="S",
                    help="only findings with this status")
    fl.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON")

    fs = findings_sub.add_parser(
        "show", help="show one finding with its report sections")
    fs.add_argument("finding_id", type=int)
    fs.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON")

    scans = sub.add_parser(
        "scans", help="read scan status from the Eyry Pro database")
    scans_sub = scans.add_subparsers(dest="scans_cmd", required=True)
    ss = scans_sub.add_parser("status", help="latest scan per scope")
    ss.add_argument("--scope", type=int, metavar="ID",
                    help="only this scope id")
    ss.add_argument("--json", action="store_true",
                    help="emit machine-readable JSON")

    up = sub.add_parser("up", help="run the whole pipeline for a scope")
    common(up)
    up.add_argument("--scope", action="append", default=[],
                    help="scope to watch, e.g. '*.example.com' (repeatable)")
    up.add_argument("--scope-file", help="file of scope patterns, one per line (e.g. a bug-bounty wildcard list)")
    up.add_argument("--tier", default=None, choices=["hot", "warm", "cold"])
    up.add_argument("--dry-run", action="store_true", help="print the pipeline and exit")
    up.add_argument("--no-review", action="store_true",
                    help="skip the Aplomado AI-review stage even if installed")
    up.add_argument("--event-sink", default=None,
                    help="write Aplomado scan events to this path (or '-' for stdout)")

    ph = sub.add_parser("pipe-hosts", help=argparse.SUPPRESS)
    ph.add_argument("--redis", default="redis://127.0.0.1:6379")
    ph.add_argument("--queue", default="purser:in")

    return p


_DISPATCH = {
    "doctor": cmd_doctor, "init": cmd_init, "status": cmd_status,
    "version": cmd_version, "up": cmd_up, "watch": cmd_watch,
    "findings": cmd_findings, "scans": cmd_scans,
}


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "pipe-hosts":
        return cmd_pipe_hosts(Config(), args)
    cfg = Config.resolve(
        redis_url=getattr(args, "redis", None),
        dsn=getattr(args, "dsn", None),
        tier=getattr(args, "tier", None),
    )
    try:
        return _DISPATCH[args.cmd](cfg, args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
