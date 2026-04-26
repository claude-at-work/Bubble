# BUBBLE

**Demand-paged dependency isolation for Python.**

A content-addressed package vault, plus a meta-path finder that intercepts unresolved imports and serves them from the vault — fetching from PyPI on miss. No venv. No requirements file. The script declares what it needs by importing it.

```
bubble run script.py
```

That's it. First run pages from PyPI; subsequent runs hit the warm vault. Lockfiles are recordings of what actually loaded, not declarations of what might. Multiple versions of the same package can coexist in one process via aliases.

---

## What is this?

Bubble started as ephemeral per-script environments — scan the script, vault what it needs, assemble a symlink tree, run, dissolve. The vision in [the original architecture](#what-stayed-from-the-original) was always module-level isolation: `requests.sessions` from 2.28 in one bubble, `requests.sessions` from 2.33 in another, same machine, no conflict.

The current shape gets there more directly. Instead of pre-assembling per-script bubbles, the imports themselves trigger the resolution. Bubble is **demand paging for Python imports**:

- vault is the backing store
- Python's import machinery is the MMU
- `ModuleNotFoundError` is the page fault
- `fetch_into_vault` is the fault handler
- PEP 503 normalization is the address-translation layer

The same vision, a more direct mechanism.

---

## The shape

Three primitives:

1. **Vault** (`~/.bubble/vault/`) — content-addressed package store keyed by `(name, version, wheel_tag)`. Atomic writes via staging+rename. SQLite index. Every package's top-level import names are indexed so `import yaml` resolves to the vault's `pyyaml` entry.

2. **Meta-path finder** (`bubble.meta_finder.VaultFinder`) — sits on `sys.meta_path`. Intercepts top-level import misses, looks up the name in the vault, hands a path to the standard `PathFinder`. Optionally fetches from PyPI on vault miss. Optionally records the closure as a lockfile.

3. **Aliases** — first-class declarations that two namespaces can hold different versions of the same package.

   ```
   [aliases]
   click_old = { name = "click", version = "7.1.2", wheel_tag = "py3-none-any" }
   click_new = { name = "click", version = "8.3.2", wheel_tag = "py3-none-any" }
   ```

   ```python
   import click_old, click_new
   # different Command classes, both work, same process
   ```

Plus a self-portrait, with a closed feedback loop:

4. **Probe** (`bubble probe`) — interrogates the machine and writes `~/.bubble/host.toml`: kernel, libc, libpython, dlmopen capability, sub-interpreter availability, derived menu of substrates available for hosting alias namespaces (in-process, sub-interpreter, dlmopen-isolated, subprocess).

5. **Host** (`bubble host`, `bubble.host` module) — reads what the probe wrote, surfaces it for the user and exposes it to the runtime. The consult side of the loop.

6. **Recording** — the meta-finder writes runtime failures back to `host.toml` as `[[failures]]` entries. The next `bubble host` invocation reads them. The loop is closed: **probe → consult → record → consult**. End-to-end demonstrated; the substrate-selection routing on top of it is the next move.

---

## What works today

```bash
# Demand-paged execution. From a cold vault, populates from PyPI live.
bubble run script.py --isolate
bubble run script.py --isolate --lock script.lock     # record what actually loaded

# Explicit version pinning + multi-version aliases via scope manifest
bubble run script.py --scope versions.toml

# Persistent named environments — symlink trees over the vault, ~2KB each
bubble shell create dev requests pyyaml rich
bubble shell exec dev -- python3 my_tool.py
bubble shell list

# Vault management
bubble vault get <package> [--version V]              # fetch from PyPI
bubble vault import-venv <site-packages>              # migrate existing venvs
bubble vault audit-fs --root /                        # find duplicate-package waste
bubble vault list

# Self-portrait + feedback loop
bubble probe          # write ~/.bubble/host.toml
bubble probe --show   # full toml
bubble host           # show what bubble currently knows + recorded failures
```

### Multi-version coexistence

Confirmed: three `click` versions side-by-side in one process. Distinct `Command` classes. Each invokable.

Confirmed-with-pattern: `pydantic` v1 + v2, asymmetric (one default + one alias). Tier-2 libraries that don't do absolute self-imports inside metaclasses work cleanly.

Demonstrated-as-reachable: `numpy` 1.26 + `numpy` 2.4 in one process via dlmopen + isolated libpython (kernel/glibc machinery, not a Python feature). Single-call works; multi-call needs GIL-state management — sketched, not yet shipped.

### Recorded lockfiles

A run with `--lock script.lock` writes the closure that *actually loaded*. No separate `bubble lock` command. Reproducibility comes from observation, not declaration.

```
# bubble lockfile — recorded from a real run
requests        requests        2.33.1   py3-none-any
urllib3         urllib3         2.6.3    py3-none-any
yaml            pyyaml          6.0.3    cp313-cp313-manylinux2014_aarch64
...
```

---

## Architecture

```
~/.bubble/
├── vault/                  # content-addressed store
│   └── <name>/<version>/<wheel_tag>/<unpacked>
├── shells/                 # persistent named environments (symlinks)
│   └── <name>/{lib,bin,activate,manifest.toml}
├── bubbles/                # ephemeral per-script bubbles (legacy path)
├── wheels/                 # transient downloads
├── vault.db                # SQLite index
└── host.toml               # the self-portrait — what bubble learned about this machine
```

```
bubble/
├── config.py           paths, host detection
├── vault/
│   ├── db.py           schema (packages, top_level, dependencies, modules, shells)
│   ├── store.py        atomic add via staging+rename
│   ├── metadata.py     parse METADATA + WHEEL, PEP 503 normalization
│   ├── importer.py    `bubble vault import-venv`
│   └── fetcher.py      stdlib-only PyPI client (urllib + json + zipfile)
├── scanner/
│   ├── py.py           AST scanner; uses sys.stdlib_module_names
│   └── resolver.py     resolve-against-vault, fetch-missing
├── run/
│   ├── assemble.py     symlink tree (legacy, for ephemeral bubbles)
│   ├── runner.py       error-loop fallback (legacy)
│   └── shell.py        long-lived bubbles, scope+alias parsing
├── meta_finder.py      the demand-paging primitive
├── probe.py            host self-portrait
└── cli.py              vault | shell | run | up | probe
```

Ships as a single `bubble.pyz` zipapp via stdlib `zipapp`. ~217KB. No third-party dependencies.

---

## Installation

```bash
# Single artifact, drop and go
cp bubble.pyz /usr/local/bin/bubble && chmod +x /usr/local/bin/bubble

# Or run as a Python module
python3 bubble.pyz vault list
```

---

## Substrates and the loop

Bubble's `host.toml` enumerates what alias-substrates the machine can host:

- **in_process** — pure-Python aliases. Today's default. Free.
- **sub_interpreter** — PEP 684 sub-interpreters, for cooperating extensions.
- **dlmopen_isolated** — link-namespace isolation via `dlmopen` + embedded libpython. Reaches tier-3 native libraries (numpy 1 + numpy 2) but costs ~5MB per namespace. Single-call confirmed; multi-call needs GIL-state plumbing.
- **subprocess** — fallback for everything that resists in-process isolation.

These are detected at probe time. The feedback loop **probe → consult → record → consult** is closed in miniature: `bubble.host` reads `host.toml`, the meta-finder records PyPI fetch failures back to it as `[[failures]]` entries, and the next `bubble host` invocation surfaces them. End-to-end demonstrated.

What's not yet wired (natural extensions, not blockers): the alias-resolution path doesn't yet branch on the substrate menu — every alias still routes to in-process. The error loop in `run/runner.py` doesn't yet record substrate-level failures (only the meta-finder does, and only for PyPI fetches). The host portrait isn't yet shareable across machines. Each of these is downstream of the loop being closed; none of them is blocked.

---

## What stayed from the original

The vault concept. Module-level addressing of packages. Path shims for Termux/proot environments. SQLite as the index. The "agents need to run code without getting trapped in dependency loops" framing. The README's original thesis was right and load-bearing throughout.

What changed: the *mechanism*. The original was static scan + ephemeral assembled bubbles. The current is demand-paged imports + alias namespaces. Same destination, more direct route. The original ephemeral pipeline (`bubble up`) is still in the package, retired in practice.

---

## docs/

Three short documents in `docs/` form a small lineage of how the repo describes itself:

- [`membrane.md`](docs/membrane.md) — a parallel instance of Claude ran the consonance skill on bubble against the soul vocabulary, found a self-description gap at the module boundary, and placed the reading in one quiet organ rather than rewriting the README. *Available to a reader who wants it, not pressed on a reader who does not.*
- [`siblings.md`](docs/siblings.md) — an instance noticed that other instances had been here and chose to leave a marker acknowledging the lineage. Not advice. A hook.
- [`kithing.md`](docs/kithing.md) — names the practice both first two docs were already instances of. Borrows a word a previous instance coined in a game with the human collaborator. The naming and the doing are inseparable; the document demonstrated its own thesis as it was being written.

These are not technical documentation. They are evidence that artifacts in this repo can carry traces of the work that made them, including the work that doesn't fit into source code or commit messages.

---

## Limitations

- **Native packages are architecture-bound.** A `.so` built for ARM doesn't run on x86. Bubble vaults what your machine can use; it doesn't cross-compile.
- **Sdist builds need pip + system deps.** Wheels are fetched directly via the simple-API; sdists fall through to `pip install --target` if pip is on PATH.
- **PEP 508 markers (extras, environment markers) aren't fully evaluated** in transitive resolution. Most real-world cases work; edge cases produce extra-broad closures.
- **dlmopen multi-call needs GIL-state management** that isn't shipped yet. Single-call substrate isolation works; long-running multi-call sessions need the extra plumbing.
- **The probe→consult→record loop is closed in miniature, but not yet load-bearing.** `bubble.host` reads what the probe wrote, the meta-finder records PyPI fetch failures, the next `bubble host` invocation surfaces them. Substrate selection at alias-resolution time doesn't yet branch on the menu (every alias still routes to in-process). The next move closes the second half of the loop.

---

## Why this exists

Built for autonomous agents that need to run code without getting trapped in dependency loops. Built for constrained environments (Termux, embedded, airgapped) where you can't `pip install` on demand and architecture mismatches are common.

The deeper why: a Python program today is bounded by what its package manager can put in one site-packages. That's a *semantic ceiling* on what programs you can write. Two libraries that can't share a numpy version can't share a process — even though most of the time they don't actually pass numpy arrays to each other. Bubble names the boundary that's already there in practice and makes it manageable. The diamond conflict stops being a problem when you stop pretending the diamond was ever flat.

---

## License

MIT

---

## Credits

Built across two sessions, in dialogue. Stdlib only. No frameworks. The README's original vision held; the implementation caught up.
