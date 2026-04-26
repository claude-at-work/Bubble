# BUBBLE

**Ephemeral Dependency Isolation**

Run any script in an isolated environment. No dependency conflicts. No version hell. No network required after the first vault.

```
bubble vault add requests
bubble up my_script.py
```

That's it. The script runs. The bubble dissolves. Next script gets a clean slate.

---

## What is this?

Bubble solves dependency hell by going atomic. Instead of managing environments (virtualenv, conda, nvm), it:

1. **Vaults** packages at the module level — not whole packages, individual files
2. **Scans** your script to find what it needs
3. **Bubbles** up an isolated environment with only those modules
4. **Dissolves** when done — no state pollution

Each bubble is isolated. `requests.sessions` from 2.28 in one bubble. `requests.sessions` from 2.33 in another. Same machine, no conflict.

---

## The Problem

You've been here:

```
Project A needs requests>=2.28
Project B needs requests<2.28
→ Dependency conflict. Choose one. The other breaks.
```

Traditional package managers manage the conflict. Bubble **avoids** it by isolating at the module level.

You've also been here:

```
Your machine: x86_64, Ubuntu
Your phone: aarch64, Termux
→ Native packages compiled for x86 won't run on ARM.
```

Bubble vaults what *you* have. If it works on your machine, it works in the bubble. Architecture-specific packages stay architecture-specific.

---

## How it works

```
┌─────────────────────────────────────────────────────────────┐
│                        BUBBLE                               │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   VAULT          SCANNER         BUBBLE                     │
│   ─────          ───────         ──────                     │
│   packages       imports         isolated                   │
│   modules        deps            environment                │
│   metadata                       run + retry                │
│                                                             │
│   ~/.bubble/vault/              ~/.bubble/bubbles/<id>/     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Three layers:**

1. **Vault** — Local cache of packages, unpacked at module level. `~/.bubble/vault/`
2. **Scanner** — Static import analysis + dependency graph resolution
3. **Bubble** — Ephemeral sandbox assembled from vault fragments

**The error loop:**

Static analysis can't catch everything (dynamic imports, conditional imports). So bubble uses a retry loop:

1. Run the script
2. Catch `ModuleNotFoundError`
3. Pull the missing module from vault (or download it)
4. Retry

Python tells you what it needs. You don't need perfect foresight.

---

## Installation

```bash
# No dependencies. Stdlib only.
cp bubble.py /usr/local/bin/bubble
chmod +x /usr/local/bin/bubble

# Or just run it directly
python3 bubble.py --help
```

---

## Quick Start

```bash
# Cache a package
bubble vault add requests

# See what's vaulted
bubble vault list

# Run a script in isolation
bubble up my_script.py

# Run and keep the bubble for inspection
bubble up my_script.py --keep

# Clean up
bubble down --all
```

---

## Commands

### `bubble vault add <package>`

Download and cache a package. Use `--version` for a specific version, `--recursive` for dependencies.

```bash
bubble vault add requests
bubble vault add requests --version 2.31.0
bubble vault add numpy --recursive
bubble vault add npm:lodash          # npm packages
```

### `bubble vault list`

Show all vaulted packages.

### `bubble vault index`

Rebuild the module index (run after manually adding packages).

### `bubble scan <script>`

Analyze a script's dependencies without running it.

```bash
bubble scan my_script.py
bubble scan my_script.py --resolve   # also check against vault
bubble scan ./my_project/             # scan a directory
```

### `bubble up <script>`

Spin up a bubble, run the script, dissolve.

```bash
bubble up my_script.py
bubble up my_script.py --keep        # keep bubble after run
bubble up my_script.py arg1 arg2     # pass arguments
bubble up ./my_project/               # package directory
```

### `bubble down`

Dissolve bubbles.

```bash
bubble down          # dissolve oldest bubble
bubble down --all    # dissolve all bubbles
```

### `bubble doctor`

Diagnose your environment. Shows Python version, platform, vault status, and path shim health.

---

## Offline First

After the initial vault, everything runs offline:

```bash
# On a connected machine
bubble vault add requests --recursive
bubble vault add numpy --recursive

# Copy ~/.bubble to offline machine
cp -r ~/.bubble /path/to/airgapped/

# Run scripts offline
bubble up my_script.py   # No network needed
```

---

## Termux / Android / proot

Bubble handles non-standard filesystem layouts:

```bash
bubble doctor
# ├─ Path shims: 8/8 resolvable
# │
# │   /etc/ssl/certs → /data/data/com.termux/files/usr/etc/tls/cert.pem
# │   /usr/lib → /usr/lib/aarch64-linux-gnu
# │   ...
```

The path shim layer bridges Termux, proot, and Alpine to where packages expect things to be.

---

## Module-Level Indexing

The vault doesn't just store packages — it indexes every module:

```bash
bubble vault list
#   requests         2.31.0    pure       2024-01-15
#   numpy            1.26.0    native     2024-01-15
#
# Total: 2 packages (1 native, 1 pure)
```

When a script imports `requests.sessions`, bubble pulls only the modules needed, not the whole `requests` package. Smaller bubbles, faster assembly.

---

## The Error Loop

Static analysis misses things. Dynamic imports, `importlib.import_module()`, conditional imports. Bubble catches these at runtime:

```python
# This script will work even though the import is dynamic
import importlib
mod = importlib.import_module('some_package')  # bubble catches the error
```

1. Scan finds static imports → vault those
2. Run → catch `ModuleNotFoundError`
3. Pull missing module → retry
4. Repeat until clean

---

## Database Schema

The vault uses SQLite for indexing:

```sql
packages        -- name, version, vault_path, has_native
modules         -- package, version, module_name, module_path
dependencies    -- package, version, dep_name, dep_version_spec
module_imports  -- package, version, module_name, imports, imports_external
```

Location: `~/.bubble/vault.db`

---

## Architecture

```
~/.bubble/
├── vault/              # Unpacked packages
│   ├── requests/
│   │   └── 2.31.0/
│   │       ├── requests/
│   │       │   ├── __init__.py
│   │       │   ├── sessions.py
│   │       │   └── ...
│   │       └── requests-2.31.0.dist-info/
│   └── numpy/
│       └── 1.26.0/
├── bubbles/            # Active ephemeral environments
│   └── a1b2c3d4e5f6/
│       ├── lib/        # Python modules (symlinked)
│       │   └── requests -> ~/.bubble/vault/requests/2.31.0/requests
│       ├── node_modules/  # JS modules (for npm)
│       └── sysroot/    # Path shims
├── wheels/             # Downloaded packages (temporary)
├── logs/               # Diagnostic logs
└── vault.db           # SQLite index
```

---

## Limitations

- **Native packages are architecture-bound** — A `.so` compiled for ARM won't run on x86. Vault on the target architecture.
- **Post-install scripts don't run** — Packages with setup hooks may need manual setup.
- **Some packages need system deps** — `libxml2`, `openssl`, etc. Install those separately.

---

## Why this exists

Built for autonomous agents that need to run code without getting trapped in dependency loops. The agent doesn't need perfect knowledge — it just needs to handle errors gracefully.

Also built for constrained environments (Termux, embedded, airgapped) where you can't `pip install` on demand and architecture mismatches are common.

---

## License

MIT

---

## Credits

Built in one session. Stdlib only. No frameworks. Just Python being Python.