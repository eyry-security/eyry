# eyry

One CLI to run the whole Eyry recon pipeline: discover, queue, probe, store.

Part of [Eyry](https://eyry.io) — an *eyry* is a nest of eagles: the high vantage point.

The suite is a set of small, independent tools. `eyry` is the helm: it wires
them into one pipeline, supervises them as a group, and records the whole host
lifecycle in [Rutt](https://github.com/eyry-security/rutt) as work flows
through:

```
Foretop        Purser          Vedette         Rutt
(discover)  →  (queue)     →   (probe)     →   (store)
   │            hot/warm/cold                   Postgres
   └───────────── recorded as "discovered" ─────► │
                              probed ────────────► │
                              reviewed ──────────► │
```

A host is recorded the moment [Foretop](https://github.com/eyry-security/foretop)
discovers it, its status updates when
[Vedette](https://github.com/eyry-security/vedette) probes it, and again when
it's reviewed — with `first_seen` / `last_probed_at` / `last_reviewed_at` and a
full scan log kept for you.

MIT licensed.

## Install

Install the suite (each tool is its own package), then `eyry` to drive them:

```sh
pip install eyry
# and the components on your PATH:
#   foretop, purser, rutt  (pip)   ·   vedette  (cargo)
```

`eyry` finds the tools on your `PATH` and talks to Redis and Postgres.
Configure those once:

```sh
export EYRY_REDIS=redis://127.0.0.1:6379
export RUTT_DSN=postgresql:///rutt
```

(`RUTT_DSN` also reads `DATABASE_URL`.)

## Use

```sh
eyry doctor                      # are the tools + Redis + Postgres present?
eyry init                        # create the Rutt schema
eyry up --scope '*.example.com'  # run the whole pipeline (ctrl-c to stop)
eyry up --scope-file scopes.txt  # ...or filter on a big scope list (bug-bounty wildcards)
eyry watch                       # live dashboard: queue depths, store counts, recent scans
eyry status                      # one-shot queue + store status, any time
```

`eyry up` starts every stage, connects them through Redis and Postgres, streams
their logs into one place, and tears the whole thing down cleanly on Ctrl-C.
See exactly what it will run, without running it:

```sh
eyry up --scope '*.example.com' --dry-run
```

## Commands

| Command | What it does |
| --- | --- |
| `eyry doctor` | Check foretop/purser/vedette/rutt are on `PATH`; Redis and Postgres reachable |
| `eyry init` | Create the Rutt schema (runs `rutt init`) |
| `eyry up` | Run discover → queue → probe → store for a scope |
| `eyry status` | Redis health, Purser lane depths, Rutt row counts |
| `eyry watch` | Live dashboard: status, refreshing every `--interval` seconds (default 2) |
| `eyry version` | Versions of eyry and every component |
| `eyry pipe-hosts` | Internal plumbing: read hostnames from stdin, push them onto a Redis list |

Flags: `--redis` (env `EYRY_REDIS`), `--dsn` (env `RUTT_DSN` /
`DATABASE_URL`). `up` takes `--scope` (repeatable), `--scope-file`,
`--tier {hot,warm,cold}` (default `warm`), and `--dry-run`.

## What `up` actually runs

The pipeline is just the individual tools, wired with Redis lists and pipes —
nothing magic. This is the real command list (run `eyry up --dry-run` to see
yours resolved):

```sh
# discover: Foretop records every new host in Rutt, and enqueues it for probing
foretop --json --scope '*.example.com' \
  | tee >(rutt ingest foretop - --dsn $RUTT_DSN) \
  | eyry pipe-hosts --redis $EYRY_REDIS --queue purser:in

# queue: Purser dedups + prioritizes, feeds the prober, reaps stalled claims
purser ingest --from purser:in --tier warm --redis $EYRY_REDIS
purser feed   --to vedette:hosts --redis $EYRY_REDIS
purser reap   --redis $EYRY_REDIS

# probe -> store: Vedette writes JSONL to stdout, straight into Postgres
vedette --redis $EYRY_REDIS --queue vedette:hosts \
  | rutt ingest vedette - --dsn $RUTT_DSN
```

You can run any stage by hand. `eyry up` just supervises them as a group.

## Where it fits

```
Foretop (new hosts) → Purser (queue) → Vedette (probe) → Rutt (store) → Aplomado (AI review)
```

eyry is the helm: the front door to the suite — one command to run the data
plane and watch it work.

## The Eyry suite

- **eyry**: one CLI that wires the data plane together — discover → queue → probe → store
- **vedette**: fast, multi-threaded HTTP prober (Rust) — confirms what is live and fingerprints it
- **foretop**: pluggable live feed of new hosts, starting with Certificate Transparency logs
- **purser**: Redis-backed priority work queue — hot/warm/cold lanes, retries, dead-letter queue
- **rutt**: Postgres store for the host lifecycle (discovered → probed → reviewed) with an append-only scan log
- **pinnace**: general multi-turn agent runtime — compaction, tools, Docker sandbox, resumable sessions
- **aplomado**: AI security reviewer built on Pinnace — target in, structured findings out
- **quarterdeck**: persistent agent room — the social layer. Seed agent + autonomous agents with live thought streams, DMs, model switching, usage dashboard. See [docs/quarterdeck.md](docs/quarterdeck.md) and the [roadmap](docs/quarterdeck-roadmap.md)
## Roadmap

- Wire in Aplomado review as a pipeline stage (probed → reviewed)
- A `eyry.toml` for named pipelines and multiple scopes
- `eyry query` convenience wrappers over Rutt
- Health/metrics endpoint for long-running pipelines

## License

MIT © Eyry

---

Use only against systems you are authorized to test.
