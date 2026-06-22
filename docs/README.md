# Meridian Documentation

Read these in order if you're new to the project:

1. **[getting-started.md](getting-started.md)** — Clone, configure credentials,
   verify the stack, hit the live Kalshi API. The first-run walkthrough.
2. **[cli-reference.md](cli-reference.md)** — Every `make` target and every
   `meridian` CLI subcommand, with arguments, options, and sample output.
3. **[architecture.md](architecture.md)** — System design, component-by-component
   responsibilities, database schema, and the rationale behind each tech choice.
4. **[kalshi.md](kalshi.md)** — Kalshi-specific integration notes: auth scheme,
   wire format, REST endpoints, WebSocket message shapes, demo-vs-prod
   considerations, and quirks discovered the hard way.
5. **[concepts.md](concepts.md)** — Financial and statistical concepts the
   project implements: order book mechanics, implied probability extraction,
   microprice, the math of binary contract pricing, and a glossary.
6. **[roadmap.md](roadmap.md)** — The phase plan, what each phase delivers,
   and the math/stats learned per phase.

## Quick links

- **First time here?** → [getting-started.md](getting-started.md)
- **"How do I run X?"** → [cli-reference.md](cli-reference.md)
- **"How does the system work?"** → [architecture.md](architecture.md)
- **"What does this Kalshi field mean?"** → [kalshi.md](kalshi.md)
- **"What's a microprice?"** → [concepts.md](concepts.md)
- **"What's coming next?"** → [roadmap.md](roadmap.md)
- **Research summary?** → [meridian-brief.md](meridian-brief.md) · [research/](research/)
- **Start agent loop?** → [../AGENT-LOOP.md](../AGENT-LOOP.md)
