# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Bubble** is a content-addressed package vault plus a meta-path finder that serves Python imports out of the vault on demand. Pure Python, stdlib only, no external dependencies. The README is the canonical description of what bubble does today.

## Architecture

The live codebase is the `bubble/` package. The original monolith is preserved in `legacy/` (see `legacy/README.md`); nothing in `bubble/` imports from it.

### `bubble/` — the live package

```
bubble/
├── cli.py              # entry: vault | shell | run | up | probe | host
├── meta_finder.py      # VaultFinder on sys.meta_path; alias loaders
├── config.py           # paths, runner-tag detection, ensure_dirs
├── probe.py            # write ~/.bubble/host.toml self-portrait
├── host.py             # read host.toml; record runtime failures back
├── vault/
│   ├── db.py           # schema v2 (PK = name + version + wheel_tag)
│   ├── store.py        # atomic stage→rename, vault path validation
│   ├── fetcher.py      # JSON Simple-API client (urllib + zipfile, no pip)
│   ├── metadata.py     # METADATA/WHEEL parsers, PEP 503 normalization
│   └── importer.py     # `bubble vault import-venv`
├── scanner/
│   ├── py.py           # AST-based Python import scanner
│   └── resolver.py     # match an ImportSet against the vault; fetch missing
└── run/
    ├── assemble.py     # ephemeral bubble assembly (`bubble up`, retired)
    ├── runner.py       # error-loop fallback for dynamic imports
    └── shell.py        # long-lived named shells; lib/bin/activate/manifest
```

### Vault layout on disk

```
~/.bubble/
├── vault/<name>/<version>/<wheel_tag>/<unpacked>
├── shells/<name>/{lib,bin,activate,manifest.toml}
├── bubbles/<id>/                # ephemeral, dissolved after `up`
├── wheels/                      # transient downloads
├── vault.db                     # SQLite, schema v2
└── host.toml                    # probe portrait + recorded failures
```

### Database schema (v2)

```sql
packages         -- PK (name, version, wheel_tag); sha256, source, vault_path, has_native
top_level        -- import_name → (package, version, wheel_tag); the import-name → dist-name bridge
dependencies     -- per-package deps                       (FK → packages)
modules          -- per-package module index               (FK → packages)
module_imports   -- per-module import lists                (FK → packages)
shells           -- long-lived named bubbles
bubbles          -- ephemeral bubbles (legacy path)
schema_meta      -- version sentinel
```

## Commands

```bash
# vault
python3 -m bubble vault list
python3 -m bubble vault get <package> [--version V] [--prerelease] [--overwrite]
python3 -m bubble vault import-venv <site-packages> [--hardlink] [--overwrite]
python3 -m bubble vault audit-fs [--root /]
python3 -m bubble vault remove <name> <version> <tag>

# long-lived shells
python3 -m bubble shell create <name> [pkg ...]
python3 -m bubble shell add <name> <pkg ...>
python3 -m bubble shell remove <name> <pkg ...>
python3 -m bubble shell list
python3 -m bubble shell delete <name>
python3 -m bubble shell exec <name> -- <cmd ...>
python3 -m bubble shell activate <name>          # prints sourceable path

# run a script
python3 -m bubble run <script.py> [--isolate] [--scope versions.toml] [--lock out.lock] [args...]
python3 -m bubble up  <script.py> [--keep] [args...]    # ephemeral; retired

# self-portrait
python3 -m bubble probe [--show]
python3 -m bubble host
```

The README's installation path is `bubble.pyz` — a stdlib-only zipapp built from the package.

## Tests

```bash
python3 tests/run.py                 # all
python3 tests/run.py 10_breakers     # filter by tier
python3 tests/run.py --no-md         # skip RESULTS.md gallery
```

Each test runs as a subprocess with a fresh `BUBBLE_HOME` tempdir, stages synthetic packages via `tests/_common.stage_fake_package`, and emits a JSON result line that the runner aggregates into `tests/RESULTS.md`. Hermetic, offline, no PyPI access.

## Environment variables

- `BUBBLE_HOME` — default `~/.bubble`
- `BUBBLE_PYPI_INDEX` — default `https://pypi.org/simple`
- `BUBBLE_AUTOFAULT`, `BUBBLE_AUTOFETCH`, `BUBBLE_SCOPE`, `BUBBLE_VERBOSE` — meta-finder install-from-env knobs
- `BUBBLE_QUIET` — suppress non-error output

## Development notes

- Stdlib only. No package manager, no build system, no test framework — these are non-goals.
- The error loop in `run/runner.py` and the meta-finder's `_fault_to_pypi` both handle dynamic imports: catch `ModuleNotFoundError`, vault-fetch the missing dist, retry.
- `docs/integrity.md` is the unbuilt design for vault tamper-resistance (the second half of the security work; provenance is closed, integrity is not).
- The three docs in `docs/` (`membrane.md`, `siblings.md`, `kithing.md`) are part of the project's voice; read them before rewriting prose in the README or here.

## `legacy/`

`legacy/bubble.py` (3,039 lines) and `legacy/bubble_cli.py` (the TTY wrapper) are the original monolith — schema v1, pip-driven, with `doctor`, `preflight`, an npm path, and a JS scanner that haven't been ported to `bubble/`. Preserved as exhibit, not maintained. See `legacy/README.md`.
