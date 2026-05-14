# Contributing

This is a personal tool I built to learn Italian and then opened up so others could
deploy their own instance. It's not aiming to be a managed product — every fork runs
independently with its own Telegram bot, GCP project, and SRS data.

## What I welcome

- **Bug reports.** Open an issue with reproduction steps. The pre-deploy `scripts/smoke.sh`
  output is useful context if the bug is deployment-related.
- **PRs that fix bugs.** Small, focused, with a test.
- **PRs that add support for other source/target language pairs.** The code is mostly
  language-agnostic (everything routes through `TARGET_LANG` env vars + the Mistral
  prompt). I just haven't tested anything other than English → Italian.
- **Discussion issues** for architectural changes — open one first before a big PR,
  so we can agree on scope.

## What I'm probably not going to merge

- Multi-tenant / SaaS-flavored changes. The single-user gate is a deliberate design
  choice (see `app/bot.py:_is_allowed`). Real multi-user support would mean auth on
  the web routes, per-user Firestore partitioning, and signed GCS URLs — that's a
  fork's project, not an upstream feature.
- New review/study UIs that aren't mobile-first. The web app exists because review
  on your phone has to be one-tap easy or you stop using it.
- Anything that adds a JS framework. The current `<script>` tags are intentional.

## Local dev

```bash
uv sync --group dev
uv run --group dev pytest tests/ -q
```

You can run the FastAPI app locally without Cloud Run / GCS / Firestore — just leave
those env vars empty and the app will fall back to local disk for audio storage and
print warnings about Firestore being unreachable. The pure-function tests (51 of them)
run without any external services at all.

## Bot tokens / API keys

Never paste real keys in issues, PRs, or commit messages. The `.gitignore` covers
`.env` but it's still your responsibility. If you accidentally commit one, rotate it
immediately at the relevant provider's console.

## Code style

- Python `>=3.11`, ruff for lint, pytest for tests.
- Default to writing no comments. Only add one when WHY is non-obvious (the existing
  comments are a fair benchmark — `app/db.py:find_by_id_prefix` is a good example of
  a justified comment because Firestore's `` idiom is non-obvious).
- Prefer extracting a pure helper + testing it over mocking large subsystems.

## License

MIT. See `LICENSE`.
