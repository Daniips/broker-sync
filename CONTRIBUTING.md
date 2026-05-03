# Contributing to tr-sync

Thanks for your interest in improving tr-sync! This guide describes how to contribute.

---

## How to report a bug

Open an issue describing:

1. **What you expected** to happen.
2. **What actually happened** (include the full console output, ideally from the `make sync` or `make renta` you're running).
3. **How to reproduce it** (Python version, operating system, command run, relevant `config.yaml` contents **without your personal data** — replace Sheet ID/ISINs/names with placeholders).
4. **Version** of `tr-sync`: `git rev-parse HEAD`.

If the bug relates to a specific TR event, run:

```bash
.venv/bin/python inspect_events.py --eventtype <TYPE>
```

and paste the resulting JSON (obfuscate any personal data first).

---

## How to propose a feature

Open an issue describing:

- The **use case** (when and why you'd need it).
- Whether you already have an idea of how to implement it.

Before you start coding, wait for someone (probably the maintainer) to validate it. Some features make sense for certain users but don't fit the overall design.

---

## How to send a Pull Request

1. **Fork** the repo and clone it.
2. **Create a descriptive branch**: `git checkout -b fix/dividend-without-isin` or `feat/csv-export`.
3. **Make sure the tests pass**: `make test`.
4. **Add tests** for your change if it's new logic. The project values tests for pure logic (parsers, FIFO, aggregators) over network tests.
5. **Commit** with descriptive messages. Follow [Conventional Commits](https://www.conventionalcommits.org/) when you can (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`).
6. **Open the PR** describing what the change does and how to test it.

### Code style

- **Python 3.11+**.
- No automatic formatter enforced, but respect the existing style (short docstrings, two lines max; `snake_case` names).
- Don't add new dependencies without justification. If you do, put the pinned version in `requirements.txt`.
- Don't add obvious comments; only when they explain the *why*, not the *what*.

### Tests

- Tests live in `test_tr_sync.py` and use `unittest` (no pytest, no network).
- If your change touches a TR event parser, add a test with a minimal fixture that reproduces TR's real JSON.
- If your change touches FIFO or aggregators, add edge cases (zero shares, fractions, dedup, mixed gifts…).

### Documentation

If your change:

- **Changes or adds a `config.yaml` field** → update `config.example.yaml` and `CONFIG.md`.
- **Changes anything in the IRPF report** → update `RENTA.md`.
- **Changes the expected Sheet structure** → update `SHEET_TEMPLATE.md`.
- **Adds a command** → update the `Makefile` (with help) and `README.md` (commands section).

---

## Local testing without touching your real Sheet

Before sending a PR, verify that your changes don't break your own sync:

```bash
make verify        # portfolio dry-run
make test          # unit tests
```

If you're going to test a full sync, consider pointing `config.yaml` at a test Sheet so you don't pollute the real one.

---

## Contact

If you have a question that doesn't fit as an issue (e.g. an architectural inquiry), open a discussion in GitHub Discussions if it's enabled, or add context in an issue tagged as `question`.

---

## License

By contributing you accept that your code is distributed under the project's MIT license.
