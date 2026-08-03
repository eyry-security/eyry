"""A tiny process supervisor for ``eyry up``.

Starts each stage as a child process in its own session (so a shell pipeline's
whole process group can be torn down cleanly), fans their output into one
prefixed, interleaved log, and shuts everything down on Ctrl-C or when a stage
dies unexpectedly.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass


@dataclass
class Stage:
    name: str
    command: str  # a shell command line (run via bash -c)


class Supervisor:
    def __init__(self, stages: list[Stage]) -> None:
        self.stages = stages
        self.procs: dict[str, subprocess.Popen] = {}
        self._stop = threading.Event()

    def _pump(self, name: str, proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            sys.stdout.write(f"[{name}] {raw.rstrip()}\n")
            sys.stdout.flush()

    def start(self) -> None:
        for st in self.stages:
            proc = subprocess.Popen(
                ["bash", "-c", st.command],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, start_new_session=True,
            )
            self.procs[st.name] = proc
            threading.Thread(target=self._pump, args=(st.name, proc), daemon=True).start()
            print(f"[eyry] started {st.name} (pid {proc.pid})", file=sys.stderr)

    def _terminate_all(self) -> None:
        for name, proc in self.procs.items():
            if proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        deadline = time.time() + 5
        for proc in self.procs.values():
            try:
                proc.wait(timeout=max(0.0, deadline - time.time()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def run(self) -> int:
        self.start()
        try:
            while True:
                time.sleep(0.4)
                for name, proc in self.procs.items():
                    rc = proc.poll()
                    if rc is not None:
                        print(f"[eyry] stage {name!r} exited (rc={rc}); shutting down",
                              file=sys.stderr)
                        return self._shutdown()
        except KeyboardInterrupt:
            print("\n[eyry] interrupted; shutting down", file=sys.stderr)
            return self._shutdown()

    def _shutdown(self) -> int:
        self._terminate_all()
        return 0
