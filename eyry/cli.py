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
import subprocess
import sys
from urllib.parse import urlparse

from . import __version__
from .config import Config
from .supervisor import Stage, Supervisor

COMPONENTS = ["foretop", "purser", "vedette", "rutt"]


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


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #

def cmd_version(cfg: Config, args) -> int:
    print(f"eyry {__version__}")
    for tool in COMPONENTS:
        v = _tool_version(tool)
        print(f"  {tool:8} {v if v else '- not found on PATH'}")
    return 0


def cmd_doctor(cfg: Config, args) -> int:
    ok = True
    print("tools:")
    for tool in COMPONENTS:
        v = _tool_version(tool)
        mark = "ok" if v else "MISSING"
        if not v:
            ok = False
        print(f"  [{mark:>7}] {tool:8} {v or 'not on PATH'}")

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


def _pipeline_stages(cfg: Config, scope: str) -> list[Stage]:
    py = sys.executable
    # discover: Foretop -> (record discovery in Rutt) + (enqueue hosts for probing)
    discover = (
        f"foretop --scope {scope!r} "
        f"| tee >(rutt ingest foretop - --dsn {cfg.dsn!r}) "
        f"| {py} -m eyry pipe-hosts --redis {cfg.redis_url!r} --queue {cfg.ingest_queue!r}"
    )
    # queue: dedup/prioritize, then feed the prober's list
    ingest = f"purser ingest --from {cfg.ingest_queue!r} --tier {cfg.tier} --redis {cfg.redis_url!r}"
    feed = f"purser feed --to {cfg.probe_queue!r} --redis {cfg.redis_url!r}"
    reap = f"purser reap --redis {cfg.redis_url!r}"
    # probe -> store: Vedette reads the list and writes JSONL to stdout (its
    # default), which streams straight into Rutt.
    probe = (
        f"vedette --redis {cfg.redis_url!r} --queue {cfg.probe_queue!r} "
        f"| rutt ingest vedette - --dsn {cfg.dsn!r}"
    )
    return [
        Stage("discover", discover),
        Stage("queue", ingest),
        Stage("feed", feed),
        Stage("reap", reap),
        Stage("probe", probe),
    ]


def cmd_up(cfg: Config, args) -> int:
    stages = _pipeline_stages(cfg, args.scope)
    if args.dry_run:
        print(f"# eyry pipeline for scope {args.scope!r}")
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

    print(f"[eyry] pipeline up for scope {args.scope!r} — ctrl-c to stop", file=sys.stderr)
    return Supervisor(stages).run()


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

    up = sub.add_parser("up", help="run the whole pipeline for a scope")
    common(up)
    up.add_argument("--scope", required=True, help="scope to watch, e.g. '*.example.com'")
    up.add_argument("--tier", default=None, choices=["hot", "warm", "cold"])
    up.add_argument("--dry-run", action="store_true", help="print the pipeline and exit")

    ph = sub.add_parser("pipe-hosts", help=argparse.SUPPRESS)
    ph.add_argument("--redis", default="redis://127.0.0.1:6379")
    ph.add_argument("--queue", default="purser:in")

    return p


_DISPATCH = {
    "doctor": cmd_doctor, "init": cmd_init, "status": cmd_status,
    "version": cmd_version, "up": cmd_up,
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
