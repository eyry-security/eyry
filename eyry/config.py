"""Shared configuration for the pipeline.

Everything the orchestrator needs to wire the stages together: where Redis and
Postgres live, and the queue names used to hand work between tools. Values come
from CLI flags, falling back to environment variables, falling back to sane
local defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Config:
    redis_url: str = "redis://127.0.0.1:6379"
    dsn: str = "postgresql:///rutt"
    ingest_queue: str = "purser:in"       # Foretop -> Purser
    probe_queue: str = "vedette:hosts"    # Purser -> Vedette
    tier: str = "warm"                    # default lane for discovered hosts

    @classmethod
    def resolve(cls, redis_url=None, dsn=None, tier=None) -> "Config":
        return cls(
            redis_url=redis_url or os.environ.get("EYRY_REDIS")
            or "redis://127.0.0.1:6379",
            dsn=dsn or os.environ.get("RUTT_DSN") or os.environ.get("DATABASE_URL")
            or "postgresql:///rutt",
            tier=tier or "warm",
        )
