# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Bubble** is an ephemeral dependency isolation tool written in pure Python (stdlib only, no external dependencies). It runs Python and Node.js scripts in isolated environments without dependency conflicts by vaulting packages at the module level.

## Architecture

### Three-Layer Design

1. **VAULT** (`~/.bubble/vault/`) — Local cache of packages unpacked at module level, indexed by SQLite (`vault.db`)
2. **SCANNER** — AST-based import extraction for Python, regex-based for JavaScript. Maps `IMPORT_TO_PACKAGE` (e.g., `PIL` → `Pillow`)
3. **BUBBLE** — Ephemeral environment created via symlinks from vault. Implements error loop: catch `ModuleNotFoundError`, pull missing module, retry

### Directory Structure

```
~/.bubble/
├── vault/              # Unpacked packages (source, not wheels)
│   └── requests/2.31.0/requests/...
├── bubbles/            # Active ephemeral environments
│   └── <id>/lib/requests -> vault symlink
├── wheels/             # Downloaded packages (temporary)
├── logs/               # Diagnostic logs
└── vault.db            # SQLite index
```

### Database Schema

```sql
packages        -- name, version, vault_path, has_native, cached_at
modules         -- package, version, module_name, module_path, size_bytes
dependencies    -- package, version, dep_name, dep_version_spec, optional
module_imports   -- package, version, module_name, imports, imports_external
```

### Main Files

- `bubble.py` — Core engine (~2790 lines), contains all logic. Run directly with `python3 bubble.py`
- `bubble_cli.py` — Simple CLI wrapper (v0.1.0)
- `bubble_cli-1.py` — Enhanced CLI with progress UI (v0.2.0)

### Key Components (in bubble.py)

- `ImportScanner(ast.NodeVisitor)` — AST visitor extracting imports from Python source
- `PATH_SHIMS` — Dictionary mapping expected paths to actual locations for proot/Termux compatibility (SSL certs, resolv.conf, lib dirs)
- `IMPORT_TO_PACKAGE` — Maps import names to PyPI package names (e.g., `PIL` → `Pillow`, `cv2` → `opencv-python`)
- `STDLIB_MODULES` / `NODE_BUILTINS` — Sets of stdlib modules that never need vaulting

## Commands

### bubble.py (core engine)

```bash
python3 bubble.py --help

# Vault operations
python3 bubble.py vault add requests [--version X.Y.Z] [--recursive]
python3 bubble.py vault add npm:lodash          # npm packages
python3 bubble.py vault list
python3 bubble.py vault index

# Scan scripts for dependencies
python3 bubble.py scan my_script.py
python3 bubble.py scan my_script.py --resolve   # check against vault

# Run in isolated bubble
python3 bubble.py up my_script.py [--keep] [args...]
python3 bubble.py up ./my_project/              # package directory

# Cleanup
python3 bubble.py down [--all]

# Diagnostics
python3 bubble.py doctor
```

### bubble_cli.py (user-friendly wrapper)

```bash
bubble <script.py> [args...]        # run in isolation
bubble get <package> [...]          # pre-cache packages (implies --recursive)
bubble get npm:lodash               # pre-cache npm package
bubble status                       # vault contents
bubble doctor                       # diagnose environment
bubble clean                        # dissolve all bubbles
bubble preflight <script.py>        # offline readiness check

# Flags
--yes / -y      auto-confirm everything
--quiet / -q    suppress output (agent mode)
--keep          preserve bubble dir after run
```

## Development Notes

- **No package manager, build system, or test framework** — pure stdlib Python
- **No dependencies** — must work with only Python stdlib
- Installation: `cp bubble.py /usr/local/bin/bubble && chmod +x`
- Environment variables: `BUBBLE_HOME` (default `~/.bubble`), `BUBBLE_ENGINE`, `PREFIX`, `TMPDIR`
- The error loop pattern handles dynamic imports: run → catch `ModuleNotFoundError` → pull missing module → retry
- Path shims bridge Termux/proot environments by mapping expected paths (e.g., `/etc/ssl/certs`) to actual locations
- Module-level assembly: only symlinks needed modules, not entire packages