# Contributing to Evolving Profile

Thank you for your interest in contributing to **Evolving Profile (EP)**!

## Ways to Contribute

- **Host Adapters**: Add or improve transcript capture, Hook bridges, or MCP recipes for new AI coding/chat hosts.
- **Storage & Retrieval**: Improve `v6/` tiering, reranking, pgvector tuning, or benchmark adapters.
- **Console UI**: Enhance the Constellation visualizations, Prompt Journey link map, or localization.
- **Documentation & Benchmarks**: Share reproducible setup guides, Windows/Linux verification reports, or evaluation datasets.

## Before Opening a Pull Request

1. Fork the repository and create a descriptive branch (`feat/...`, `fix/...`, `docs/...`).
2. Keep personal data, paths, credentials, and private memory banks strictly out of commits.
3. Run the release preflight and secret checks from the repository root:

```bash
python3 scripts/release-preflight.py --root . --json
./scripts/verify-package.sh
```

4. Run the relevant unit tests for any module you modify (`host-adapter`, `guidance`, `controller`, `v6`, `console`, or `api`).
5. Preserve `LICENSE` and `NOTICE.md` attribution, including upstream Hindsight credits.

## Reporting Issues

Please use the provided GitHub issue templates and include your OS, Python/Node version, AI host, and sanitized logs.
