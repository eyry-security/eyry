# eyry

One CLI to run the [Eyry](https://eyry.io) recon suite together.

The suite is a set of small, independent tools. `eyry` wires them into one
pipeline and records the whole host lifecycle as work flows through:

```
Foretop        Purser          Vedette         Rutt
(discover)  →  (queue)     →   (probe)     →   (store)
   │            hot/warm/cold                   Postgres
   └───────────── recorded as "discovered" ─────► │
                              probed ────────────► │
                              reviewed ──────────► │
```

Every stage writes to [Rutt](https://github.com/eyry-security/rutt): a host is
recorded the moment [Foretop](https://github.com/eyry-security/foretop)
discovers it, its status updates when [Vedette](https://github.com/eyry-security/vedette)
probes it, and again when it's reviewed — with `first_seen` / `last_probed` /
`last_reviewed` and a full scan log kept for you.

MIT licensed.

## Install

Install the suite (each tool is its own package), then eyry to drive them:

```sh
pip install eyry
# and the components on your PATH:
#   foretop, purser, rutt  (pip)   ·   vedette  (cargo)
```

eyry finds the tools on your `PATH` and talks to Redis and Postgres. Configure
those once:

```sh
export EYRY_REDIS=redis://127.0.0.1:6379
export RUTT_DSN=postgresql:///rutt
```

## Use

```sh
eyry doctor                      # are the tools + Redis + Postgres present?
eyry init                        # create the Rutt schema
eyry up --scope '*.example.com'  # run the whole pipeline (ctrl-c to stop)
eyry status                      # queue depths + store counts, any time
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
| `eyry doctor` | Check each tool is on `PATH` and Redis/Postgres are reachable |
| `eyry init` | Create the Rutt schema |
| `eyry up --scope <s>` | Run discover → queue → probe → store (`--tier`, `--dry-run`) |
| `eyry status` | Redis health, queue depths (Purser), row counts (Rutt) |
| `eyry version` | Versions of eyry and every component |

Flags: `--redis` (env `EYRY_REDIS`), `--dsn` (env `RUTT_DSN`).

## What `up` actually runs

The pipeline is just the individual tools, wired with Redis lists and pipes:

```sh
# discover: record every new host in Rutt, and enqueue it for probing
foretop --scope '*.example.com' \
  | tee >(rutt ingest foretop -) \
  | eyry pipe-hosts --queue purser:in

# queue: dedup + prioritize, then feed the prober
purser ingest --from purser:in --tier warm
purser feed --to vedette:hosts
purser reap

# probe -> store: Vedette writes JSONL to stdout, straight into Postgres
vedette --redis redis://127.0.0.1:6379 --queue vedette:hosts \
  | rutt ingest vedette -
```

Nothing magic — you can run any stage by hand. `eyry up` just supervises them as
a group.

## Where it fits

eyry is the top-level entry point to the suite:

- **Foretop** — discover new hosts (certstream)
- **Purser** — priority queue with retries and a DLQ
- **Vedette** — fast HTTP prober
- **Rutt** — Postgres store with the host lifecycle
- **Pinnace / Aplomado / Quarterdeck** — agent runtime, AI scanner, and control
  plane (they plug into the same Rutt store)

See the whole suite at [github.com/eyry-security](https://github.com/eyry-security).

## Roadmap

- Wire in Aplomado review as a pipeline stage (probed → reviewed)
- A `eyry.toml` for named pipelines and multiple scopes
- `eyry query` convenience wrappers over Rutt
- Health/metrics endpoint for long-running pipelines

## License

MIT © Eyry
