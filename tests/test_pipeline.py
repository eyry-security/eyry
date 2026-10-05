"""Tests for the pipeline stage wiring — no Docker, no Redis, no Postgres.

Validates that _pipeline_stages() produces the right commands and that the
Aplomado review stage is present/absent based on --no-review and whether
aplomado is on PATH.
"""

from __future__ import annotations

from unittest.mock import patch

from eyry.cli import _pipeline_stages, build_parser, COMPONENTS, OPTIONAL_COMPONENTS
from eyry.config import Config


def _cfg() -> Config:
    return Config(
        redis_url="redis://127.0.0.1:6379",
        dsn="postgresql:///rutt",
    )


# --------------------------------------------------------------------------- #
# _pipeline_stages: aplomado present
# --------------------------------------------------------------------------- #

def test_stages_with_aplomado_includes_review():
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(
            _cfg(), ["*.example.com"], None, review=True, event_sink="/tmp/events.jsonl"
        )
    names = [s.name for s in stages]
    assert "probe+review" in names
    assert "probe" not in names  # replaced by probe+review


def test_review_stage_command_includes_aplomado():
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(
            _cfg(), ["*.example.com"], None, review=True
        )
    review = next(s for s in stages if s.name == "probe+review")
    assert "aplomado scan" in review.command
    assert "--rutt-dsn" in review.command


def test_review_stage_includes_event_sink():
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(
            _cfg(), ["*.example.com"], None, review=True, event_sink="/tmp/events.jsonl"
        )
    review = next(s for s in stages if s.name == "probe+review")
    assert "--event-sink" in review.command
    assert "/tmp/events.jsonl" in review.command


def test_review_stage_no_event_sink_when_none():
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(
            _cfg(), ["*.example.com"], None, review=True, event_sink=None
        )
    review = next(s for s in stages if s.name == "probe+review")
    assert "--event-sink" not in review.command


def test_review_stage_tees_to_rutt():
    """Probe output goes to both rutt ingest AND aplomado."""
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(
            _cfg(), ["*.example.com"], None, review=True
        )
    review = next(s for s in stages if s.name == "probe+review")
    assert "rutt ingest vedette" in review.command
    assert "tee" in review.command


# --------------------------------------------------------------------------- #
# _pipeline_stages: aplomado absent or --no-review
# --------------------------------------------------------------------------- #

def test_stages_without_aplomado_skips_review():
    def which(tool):
        if tool == "aplomado":
            return None
        return f"/usr/bin/{tool}"

    with patch("eyry.cli.shutil.which", side_effect=which):
        stages = _pipeline_stages(_cfg(), ["*.example.com"], None, review=True)
    names = [s.name for s in stages]
    assert "probe" in names
    assert "probe+review" not in names


def test_stages_no_review_flag_skips_aplomado():
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(
            _cfg(), ["*.example.com"], None, review=False
        )
    names = [s.name for s in stages]
    assert "probe" in names
    assert "probe+review" not in names


def test_probe_only_stage_pipes_to_rutt():
    """Without review, probe pipes directly to rutt ingest (original behavior)."""
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(
            _cfg(), ["*.example.com"], None, review=False
        )
    probe = next(s for s in stages if s.name == "probe")
    assert "rutt ingest vedette" in probe.command
    assert "aplomado" not in probe.command


# --------------------------------------------------------------------------- #
# Other stage behavior unchanged
# --------------------------------------------------------------------------- #

def test_core_stages_always_present():
    """discover, queue, feed, reap are always present regardless of review."""
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(_cfg(), ["*.example.com"], None, review=True)
    names = [s.name for s in stages]
    assert "discover" in names
    assert "queue" in names
    assert "feed" in names
    assert "reap" in names


def test_stage_count_with_review():
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(_cfg(), ["*.example.com"], None, review=True)
    assert len(stages) == 5  # discover, queue, feed, reap, probe+review


def test_stage_count_without_review():
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(_cfg(), ["*.example.com"], None, review=False)
    assert len(stages) == 5  # discover, queue, feed, reap, probe


def test_scope_file_passed_to_discover():
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(
            _cfg(), [], "scopes.txt", review=False
        )
    discover = next(s for s in stages if s.name == "discover")
    assert "--scope-file" in discover.command
    assert "scopes.txt" in discover.command


# --------------------------------------------------------------------------- #
# CLI parser: new flags
# --------------------------------------------------------------------------- #

def test_cli_parser_no_review_flag():
    args = build_parser().parse_args([
        "up", "--scope", "*.example.com", "--no-review",
    ])
    assert args.no_review is True


def test_cli_parser_event_sink_flag():
    args = build_parser().parse_args([
        "up", "--scope", "*.example.com", "--event-sink", "/tmp/events.jsonl",
    ])
    assert args.event_sink == "/tmp/events.jsonl"


def test_cli_parser_defaults():
    args = build_parser().parse_args(["up", "--scope", "*.example.com"])
    assert args.no_review is False
    assert args.event_sink is None


# --------------------------------------------------------------------------- #
# COMPONENTS lists
# --------------------------------------------------------------------------- #

def test_aplomado_is_optional():
    assert "aplomado" in OPTIONAL_COMPONENTS
    assert "aplomado" not in COMPONENTS


def test_required_components_unchanged():
    assert COMPONENTS == ["foretop", "purser", "vedette", "rutt"]


# --------------------------------------------------------------------------- #
# Foretop -> queue handoff
# --------------------------------------------------------------------------- #

def test_discover_requests_foretop_json():
    """Foretop's default is bare hosts; both Eyry consumers require JSONL."""
    with patch("eyry.cli.shutil.which", side_effect=lambda t: f"/usr/bin/{t}"):
        stages = _pipeline_stages(_cfg(), ["*.example.com"], None, review=False)
    discover = next(s for s in stages if s.name == "discover")
    assert discover.command.startswith("foretop --json ")
def test_pipe_hosts_enqueues_foretop_json(monkeypatch, capsys):
    """A Foretop JSONL record reaches the configured probe-ingest queue."""
    import io
    import sys
    from types import SimpleNamespace

    from eyry.cli import cmd_pipe_hosts

    pushed = []

    class FakeClient:
        def lpush(self, queue, host):
            pushed.append((queue, host))

    class FakeRedis:
        @staticmethod
        def from_url(url, decode_responses):
            assert url == "redis://smoke/15"
            assert decode_responses is True
            return FakeClient()

    monkeypatch.setitem(sys.modules, "redis", FakeRedis)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            '{"host":"127.0.0.1:5000","source":"certstream"}\n'
            "not-json\n"
            '{"source":"certstream"}\n'
        ),
    )

    args = SimpleNamespace(redis="redis://smoke/15", queue="smoke:ingest")
    assert cmd_pipe_hosts(_cfg(), args) == 0
    assert pushed == [("smoke:ingest", "127.0.0.1:5000")]
    assert "enqueued 1 host(s)" in capsys.readouterr().err
