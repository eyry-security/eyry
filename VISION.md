# The Eyry Suite — Full Vision

> **See your attack surface the moment it changes.**

Authored 2026-10-04. This is the canonical statement of what we're building:
eight tools, one system, and the product they grow into. Read it before you
touch any repo. (Build plan: `DIRECTION.md`. Task board: `COORDINATION.md` —
both live on the dev droplet.)

---

## The thesis

Attack surfaces are alive. Certificates get issued, subdomains spin up,
services deploy, configs drift — and the window between *"something new
appeared"* and *"someone noticed"* is where bug bounty hunters win or lose,
where red teams get in, and where defenders are always too late.

Today your options are:

1. **Point-in-time manual recon** — fast for one target, useless for watching
   a scope continuously.
2. **Enterprise ASM platforms** — bloated, expensive, closed, and built for
   compliance checklists, not hackers.

Eyry is the third option: **an open-source, always-on recon system that watches
your scope, notices the moment something changes, figures out what's worth a
closer look, puts an AI agent in front of it, and tells you in chat.** Every
piece is independently useful and independently launched, so the suite grows an
audience one tool at a time — and the whole thing compounds into a product that
can be hosted as a service.

---

## The eight tools

Three bands. Each repo stands alone; together they're a system.

### The data plane — watch, queue, probe, remember

**Foretop — the lookout.** A pluggable live feed of new hosts for a scope you
define. First source: Certificate Transparency logs (the moment someone gets a
cert for `new-thing.example.com`, Foretop sees it — often before the service is
fully live). Later sources: passive DNS, subdomain enumeration, ASN/CIDR
ranges, wordlists, third-party APIs, static lists. One interface, many feeds.
*What it's not:* a port scanner, a crawler, or a "run once" enumeration tool.
It's a standing watch.

**Purser — the quartermaster.** A Redis-backed priority work queue: hot / warm /
cold lanes, retries, a dead-letter queue, dedup, backpressure, and at-least-once
delivery via a claim/ack cycle. Turns a firehose of discovered hosts into an
orderly, survivable work stream. *What it's not:* a message broker — no server
to run beyond Redis itself, no topic routing, no stream processing. Queues with
priorities, retries, and a DLQ. That's it, and it's exactly enough.

**Vedette — the forward scout.** A fast, multi-threaded HTTP prober (Rust):
confirm what's live, fingerprint it (status, server, title, tech, TLS), and move
on. Reads from stdin, files, or Redis; writes JSONL that flows straight
downstream. *What it's not:* a vulnerability scanner. It answers one question,
fast and at scale: *what's alive and what is it?*

**Rutt — the logbook.** A Postgres store for the host lifecycle:
discovered → probed → reviewed, with `first_seen` / `last_seen` /
`last_probed_at` / `last_reviewed_at`, full probe records, findings, and an
append-only scan log of everything that touched every host. Months later you
can ask "what was in scope, running nginx, and returned a 200?" *What it's
not:* a SIEM. It's the suite's shared memory, queryable by humans and agents
alike.

**eyry — the helm.** One CLI that wires the data plane together and supervises
it: `eyry up --scope '*.example.com'` runs discover → queue → probe → store,
streams the logs in one place, and tears down cleanly. It also records the
lifecycle as work flows through Rutt. *What it's not:* a framework or a config
language. It's the front door: install the suite, run one command, watch it work.

### The agent plane — decide, investigate

**Pinnace — the engine.** A general-purpose, multi-turn agent runtime (Python):
tool calling, context compaction for long runs, a built-in toolset executed in a
Docker sandbox, persistent resumable sessions, and a `finish()` convention that
ends runs with structured results instead of vibes. Deliberately
*not* a security tool — it's the keystone everything agentic builds on, and its
generality is its growth engine: "point a sandboxed agent at anything" is a
story that travels far beyond security. *What it's not:* a framework with
twelve abstractions. One agent, one run, durable.

**Aplomado — the falcon.** An AI security reviewer built on Pinnace: take a
target (from Vedette, in the shared schema), investigate it inside a sandbox,
emit structured findings with severity, confidence, evidence, and remediation.
The offensive hero tool — "an AI that reviews a target and reports." *What
it's not:* a replacement for human judgment, an autopwner, or a vuln scanner
with a chat window. It reviews, documents, and hands off; humans decide.

### The control plane — persist, schedule, talk

**Quarterdeck — the command deck.** The control plane for Pinnace agents and
the whole system: a scheduler / task manager / event-driven runtime that wakes
and sleeps agents on triggers (schedule, new host from Foretop, finding from
Aplomado, inbound message, webhook); persistent agent identity and lifecycle on
top of Pinnace sessions; **inter-agent communication via an IRC-style chat** —
agents talk to each other and to you, share findings, hand off tasks; and
**ChatOps** over Slack/Discord: alerts, "what's new on my scope?", "scan this",
"summarize findings." It also orchestrates the recon pipeline as one of the
workloads it schedules and reacts to.

This is **one product, not two**: persistent chat-agents and pipeline
orchestration are the same system. Quarterdeck schedules work, routes events,
runs Pinnace agents, and hosts the chat they live in. And it's **the seed of
the hosted SaaS** — the thing that turns "a pipeline you run" into "monitoring
you subscribe to." *What it's not (yet):* the SaaS itself. It's the
self-hosted control plane first; the hosted offering grows out of it.

---

## The end state

Picture the finished system, watching a bug-bounty scope:

```
Certificate issued for api-staging.example.com
        │  (Foretop sees it in the CT stream, seconds later)
        ▼
Purser: hot lane — new host on a watched scope
        │  (dedup: never seen; priority: new + in-scope)
        ▼
Vedette: alive. 200. nginx. "Example API". TLS cert details.
        │  (Rutt records: discovered → probed, scan log appended)
        ▼
Aplomado: sandboxed review. Two findings: exposed .git (high),
          verbose error pages (low). Evidence attached.
        │  (Rutt records: probed → reviewed)
        ▼
Quarterdeck → Slack: "🛰 New host on *.example.com: api-staging.example.com
  (200, nginx). AI review found 1 high: exposed .git. Full report →"
```

Continuous, always-on, no human in the loop until there's something worth a
human. That loop — **notice → probe → review → alert** — is the product. The
eight tools are just the cleanest way we found to build it: each one useful
alone, each one a public launch, all of them speaking the same schema.

Beyond security, the same control plane is a general story: *a scheduler that
wakes and sleeps persistent AI agents and lets them talk to each other and to
you.* That's extra top-of-funnel for the same product — and it's why Pinnace
stays general while Aplomado carries the security behavior.

---

## How the suite grows (the growth engine)

Open-source-led growth is the strategy, and the architecture serves it:

- **One repo per tool (polyrepo).** Each is its own star magnet, its own
  README, its own entry point, its own "we shipped X" launch moment. A
  monorepo would be cleaner to develop and weaker to market. We chose reach.
- **Release order compounds.** Vedette first (fast, instantly useful, strong
  debut — the classic first viral tool), then Pinnace (the general agent
  runtime travels beyond security), then Aplomado (first agentic security tool),
  then Foretop, Purser, Rutt, the `eyry` CLI wiring them together, and finally
  Quarterdeck tying it all into the control plane. Ship 1–2 to build an
  audience before the full system exists; every later release rides the last
  one's momentum.
- **Independently useful, Unix-y composable.** Every tool runs alone on the
  CLI (stdin/stdout, JSONL) *and* slots into the orchestrated system. A hacker
  should be able to adopt one tool on a Tuesday without buying the suite.
- **Shared result schema.** Host/service/finding schema defined once, early.
  Prober output, queue payloads, scanner input, and store records all speak it.
  This is the glue between layers — and the thing that lets third-party tools
  plug in later.
- **Right language per layer.** Rust where speed matters (Vedette), Python
  where the AI ecosystem lives (Pinnace, Aplomado, Quarterdeck), Redis +
  Postgres for the stateful middle. No rewrites for ideology's sake.
- **MIT everywhere.** Permissive licensing maximizes adoption, forks, and
  contributions. The moat is the system and the audience, not the license.

---

## Milestones

Where we are and where we're going (operational detail lives in
`COORDINATION.md` on the dev droplet):

1. **v0 data plane — done.** Foretop, Vedette, Purser, Rutt, and the `eyry` CLI
   work end-to-end: discover → queue → probe → store, with the host lifecycle
   tracked. Dogfooding on real bug-bounty scopes next — harden from what breaks.
2. **v0 agent plane — converging.** Pinnace converges on one implementation
   (sessions are load-bearing for Quarterdeck); Aplomado converges the same way.
   Default model: `claude-opus-4-6` via Anthropic, provider-configurable, with
   DigitalOcean serverless inference as a cost-down option once verified.
3. **v1 per tool — the launch sequence.** Harden each tool from dogfooding,
   then ship them one by one as public OSS launches: Vedette → Pinnace →
   Aplomado → Foretop → Purser → Rutt → `eyry` CLI. Each launch is a marketing
   event with docs, examples, and a story.
4. **Pipeline v1 + Aplomado stage.** Wire the AI review into `eyry up` as a
   real stage (probed → reviewed), add `eyry.toml` for named pipelines and
   multiple scopes.
5. **Quarterdeck beta.** Scheduler, wake/sleep, agent identity + memory,
   inter-agent chat, ChatOps (Slack/Discord), pipeline orchestration.
   Self-hosted first.
6. **Eyry Pro — Quarterdeck as a service.** The whole system, hosted:
   point it at your scopes and get continuous attack-surface monitoring with
   AI review and chat alerts, in a webapp that feels like eyry.io. Pricing is
   prepaid token credits — deposit $10 to start, then it's purely token spend.
   No subscriptions, no tiers, no surprise bills: you can't spend what you
   haven't deposited, and every token is visible on the meter. Deep prompt
   caching keeps costs down across the board. The OSS suite is the funnel;
   this is the business.

   Pro build sequence:
   - **Token metering.** Per-customer, per-agent usage tracking in
     Quarterdeck/Pinnace. Every inference call logged with model, tokens,
     and cost basis. The meter is the product — it has to be exact.
   - **Agent webchat.** KiwiIRC-style web room where persistent named agents
     live. Presence, channels, DMs. Each agent keeps its own memory.md,
     runbook.md, identify.md and compacts proactively. Agents wake on
     schedule, create/manage/subscribe channels, DM each other, and
     administrate their own instance.
   - **Prepaid billing.** Deposit flow ($10 minimum), balance tracking,
     spend-down on token usage. Optional autorefill when balance runs low.
     Stripe for deposits. No overdraft possible by construction.
   - **Pro webapp.** The full dashboard matching eyry.io branding — scopes,
     findings, agent room, usage meter, billing. The destination the OSS
     funnel leads to.
   - **CLI bridge.** The open-source `eyry` CLI connects to a Pro instance:
     authenticate once and your local workflow spends token credits against
     hosted infrastructure. No workflow change to upgrade from OSS to Pro.

---

## Principles (the short list)

1. Every component is independently useful and independently released.
2. Composable, Unix-y interfaces — stdin/stdout, JSONL, shared schema.
3. Shared result schema, defined once, early.
4. Right language per layer; no rewrites for ideology.
5. General core, security on top (Pinnace is general; Aplomado is the security
   behavior on top of it).
6. Builder-to-builder voice: direct, technical, no buzzwords. Show the command,
   show the output, get out of the way.
7. MIT licensed across the suite.

## Names

All nautical, all vetted against security tools and package registries. Locked
2026-08-02.

| Tool | The name means |
| --- | --- |
| Eyry | The brand — a nest of eagles. The high vantage point. |
| Vedette | The forward scout / picket boat sent ahead of the fleet. |
| Foretop | The lookout platform high on the mast — first to spot new arrivals. |
| Purser | The officer who allocates and distributes provisions — the quartermaster. |
| Rutt | A mariner's *rutter*: the logbook of a voyage, every coast and hazard seen. |
| Pinnace | The ship's boat, dispatched to do independent work. |
| Aplomado | The aplomado falcon — small, fast, strikes precisely. |
| Quarterdeck | The command deck where the ship is run from. |

## Open questions

- **SaaS shape:** decided — self-serve prepaid token credits (Eyry Pro).
  Deposit to start, purely usage-based spend, fully transparent meter.
- **Foretop plugin SDK:** exact shape of the source-plugin interface (first
  source beyond certstream decides it).
- **Shared schema package:** polyrepo + a small shared `schema`/`types`
  package everyone depends on (leaning yes).
- **Sandbox model:** Docker only, or pluggable (Docker / gVisor / firecracker)
  later?
- **Decider models:** parked until the chat-model path is proven (DO inference
  catalog lists options; verify before trusting).
- **Scope ethics:** read each program's automated-scanning policy before
  probing; stay in scope. Some scopes carry extra policy constraints —
  when in doubt, check first.

---

*Eyry Cyber Security. Open-source recon and offensive security tooling for bug
bounty hunters, red teamers, and pentesters. [eyry.io](https://eyry.io) ·
[github.com/eyry-security](https://github.com/eyry-security)*
