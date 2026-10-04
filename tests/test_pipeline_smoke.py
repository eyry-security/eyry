"""End-to-end smoke coverage for the Foretop -> Vedette -> Aplomado flow.

The test runs the real ``eyry up`` CLI and supervisor. Small executable shims
stand in for component process boundaries so the data contract is deterministic
and does not require external services or a model call. Set
``EYRY_SMOKE_TARGET=127.0.0.1:5000`` to exercise the droplet's local fixture.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socketserver
import subprocess
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _VulnerableTarget(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/.git/HEAD":
            body = b"ref: refs/heads/main\n"
            self.send_response(200)
        else:
            body = b"not found\n"
            self.send_response(404)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass


class _RedisPing(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.recv(64)
        self.request.sendall(b"+PONG\r\n")


class _PingServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


def _write_executable(path: Path, source: str) -> None:
    path.write_text(textwrap.dedent(source).lstrip(), encoding="utf-8")
    path.chmod(0o755)


def _start_server(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def test_foretop_vedette_aplomado_pipeline_smoke(tmp_path):
    target_server = None
    target = os.environ.get("EYRY_SMOKE_TARGET")
    if not target:
        target_server = ThreadingHTTPServer(("127.0.0.1", 0), _VulnerableTarget)
        _start_server(target_server)
        target = f"127.0.0.1:{target_server.server_address[1]}"

    ping_server = _PingServer(("127.0.0.1", 0), _RedisPing)
    _start_server(ping_server)

    bin_dir = tmp_path / "bin"
    module_dir = tmp_path / "modules"
    bin_dir.mkdir()
    module_dir.mkdir()
    queue_path = tmp_path / "queue.json"
    store_path = tmp_path / "rutt.jsonl"
    event_path = tmp_path / "events.jsonl"

    _write_executable(
        bin_dir / "foretop",
        """
        #!/usr/bin/env python3
        import json
        import os
        import sys
        import time

        if "--json" not in sys.argv:
            raise SystemExit("smoke foretop requires --json")
        print(json.dumps({"host": os.environ["SMOKE_TARGET"], "source": "smoke"}), flush=True)
        time.sleep(20)
        """,
    )
    _write_executable(
        bin_dir / "purser",
        """
        #!/usr/bin/env python3
        import time
        time.sleep(20)
        """,
    )
    _write_executable(
        bin_dir / "rutt",
        """
        #!/usr/bin/env python3
        import json
        import os
        import sys

        kind = sys.argv[2]
        for line in sys.stdin:
            record = json.loads(line)
            with open(os.environ["SMOKE_STORE"], "a", encoding="utf-8") as output:
                output.write(json.dumps({"kind": kind, "record": record}) + "\\n")
        """,
    )
    _write_executable(
        bin_dir / "vedette",
        """
        #!/usr/bin/env python3
        import json
        import os
        import time
        from urllib.request import urlopen

        queue_path = os.environ["SMOKE_QUEUE"]
        for _ in range(100):
            try:
                with open(queue_path, encoding="utf-8") as source:
                    queued = json.load(source)
                break
            except (FileNotFoundError, json.JSONDecodeError):
                time.sleep(0.05)
        else:
            raise SystemExit("timed out waiting for Eyry queue handoff")

        url = f"http://{queued['host']}/.git/HEAD"
        with urlopen(url, timeout=3) as response:
            body = response.read().decode("utf-8", "replace")
            print(json.dumps({
                "host": queued["host"],
                "url": url,
                "status": response.status,
                "body": body,
            }), flush=True)
        """,
    )
    _write_executable(
        bin_dir / "aplomado",
        """
        #!/usr/bin/env python3
        import json
        import sys

        args = sys.argv[1:]
        sink = args[args.index("--event-sink") + 1]
        probe = json.loads(next(sys.stdin))
        findings = []
        if probe["status"] == 200 and probe["body"].startswith("ref: refs/heads/"):
            findings.append({
                "title": "Exposed version control metadata",
                "severity": "high",
                "target": probe["url"],
            })
        event = {
            "type": "aplomado.scan.completed",
            "data": {"target": probe["host"], "findings": findings},
        }
        with open(sink, "w", encoding="utf-8") as output:
            output.write(json.dumps(event) + "\\n")
        """,
    )
    (module_dir / "redis.py").write_text(
        textwrap.dedent(
            """
            import json
            import os

            class _Client:
                def lpush(self, queue, host):
                    path = os.environ["SMOKE_QUEUE"]
                    temporary = path + ".tmp"
                    with open(temporary, "w", encoding="utf-8") as output:
                        json.dump({"queue": queue, "host": host}, output)
                    os.replace(temporary, path)

            def from_url(url, decode_responses=False):
                return _Client()
            """
        ),
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{bin_dir}:{env.get('PATH', '')}",
            "PYTHONPATH": f"{module_dir}:{repo_root}",
            "SMOKE_TARGET": target,
            "SMOKE_QUEUE": str(queue_path),
            "SMOKE_STORE": str(store_path),
        }
    )
    redis_url = f"redis://127.0.0.1:{ping_server.server_address[1]}"

    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "eyry",
                "up",
                "--scope",
                target,
                "--redis",
                redis_url,
                "--dsn",
                "postgresql:///smoke",
                "--event-sink",
                str(event_path),
            ],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=15,
        )
    finally:
        ping_server.shutdown()
        ping_server.server_close()
        if target_server is not None:
            target_server.shutdown()
            target_server.server_close()

    assert result.returncode == 0, result.stdout + result.stderr
    event = json.loads(event_path.read_text(encoding="utf-8"))
    assert event["type"] == "aplomado.scan.completed"
    assert event["data"]["target"] == target
    assert event["data"]["findings"] == [
        {
            "title": "Exposed version control metadata",
            "severity": "high",
            "target": f"http://{target}/.git/HEAD",
        }
    ]

    queued = json.loads(queue_path.read_text(encoding="utf-8"))
    assert queued == {"queue": "purser:in", "host": target}
    stored = [json.loads(line) for line in store_path.read_text(encoding="utf-8").splitlines()]
    assert {item["kind"] for item in stored} == {"foretop", "vedette"}
