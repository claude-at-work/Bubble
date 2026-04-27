"""Vault store operations: add/remove/lookup with atomic writes.

`add_from_directory` is the unified entry point. Caller stages an unpacked
package directory anywhere; we move it to .staging, then rename into place.
A failed add leaves no half-populated vault entry.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .. import config
from . import db


# Strict allowlist for vault path segments. PEP 503 names are alnum/./-/_;
# wheel tags use the same plus '+'. Reject anything else, including null bytes.
_VAULT_SEG_RE = re.compile(r"^[A-Za-z0-9._+-]{1,128}$")


def _safe_segment(s: str, kind: str) -> str:
    # The character class admits "." and ".." which would resolve under the
    # parent. Defense in depth: reject them at source even though is_under_vault
    # would also catch the actual escape downstream.
    if s in {".", ".."} or not _VAULT_SEG_RE.match(s):
        raise ValueError(f"unsafe vault {kind!r}: {s!r}")
    return s


# Module-level audit log. Every time vault-add inserts a top_level row whose
# import_name is already claimed by a different (package, version, wheel_tag),
# we append a structured entry here. First-claim semantics are unchanged
# (meta_finder picks by version/tag); the log just makes the contention
# observable instead of a silent accident.
top_level_contentions: list[dict] = []


def vault_path_for(name: str, version: str, wheel_tag: str) -> Path:
    return (config.VAULT_DIR
            / _safe_segment(name, "name")
            / _safe_segment(version, "version")
            / _safe_segment(wheel_tag, "wheel_tag"))


def is_under_vault(path: Path) -> bool:
    """Confirm a path resolves inside VAULT_DIR. Use before linking from a DB-supplied path."""
    try:
        resolved = Path(path).resolve()
        vault_root = config.VAULT_DIR.resolve()
    except OSError:
        return False
    return resolved == vault_root or vault_root in resolved.parents


def has(conn: sqlite3.Connection, name: str, version: str, wheel_tag: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM packages WHERE name=? AND version=? AND wheel_tag=?",
        (name, version, wheel_tag),
    ).fetchone()
    return row is not None


def find_versions(conn: sqlite3.Connection, name: str) -> list[tuple[str, str, str]]:
    """Return [(version, wheel_tag, vault_path), ...] for a package name."""
    return list(conn.execute(
        "SELECT version, wheel_tag, vault_path FROM packages WHERE name=? "
        "ORDER BY version DESC, wheel_tag",
        (name,),
    ).fetchall())


def stage_dir() -> Path:
    """Allocate a fresh staging directory. 0o700 — package payloads can include
    files users wouldn't expect to be world-readable (tokens in dist-info, etc)."""
    config.STAGING_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    p = config.STAGING_DIR / uuid.uuid4().hex
    p.mkdir(mode=0o700)
    return p


def _detect_native(root: Path) -> bool:
    for ext in (".so", ".dylib", ".pyd"):
        if next(root.rglob(f"*{ext}"), None):
            return True
    return False


def _hash_file(p: Path) -> bytes:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        while True:
            chunk = fh.read(64 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.digest()


def _hash_subtree(root: Path) -> str:
    """Deterministic content hash over a file or directory subtree.

    Same bytes under the same relative paths → same digest. Any file
    addition, removal, rename, or content change changes the digest. This
    is the cryptographic edge between an import name and the bytes the
    vault serves under it.
    """
    h = hashlib.sha256()
    if root.is_file():
        h.update(b"FILE\x00\x00")
        h.update(_hash_file(root))
        return h.hexdigest()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix().encode("utf-8")
        if p.is_dir():
            h.update(b"DIR\x00")
            h.update(rel)
            h.update(b"\x00")
        elif p.is_file():
            h.update(b"FILE\x00")
            h.update(rel)
            h.update(b"\x00")
            h.update(_hash_file(p))
    return h.hexdigest()


def _top_level_subpath(vault_path: Path, name: str) -> Optional[Path]:
    """Resolve an import name to a real subpath in the vault tree.

    Used in verify-mode: a top_level.txt assertion is recorded only if the
    name corresponds to a real importable target.
    """
    pkg = vault_path / name
    if pkg.is_dir() and (pkg / "__init__.py").exists():
        return pkg
    py = vault_path / f"{name}.py"
    if py.is_file():
        return py
    for entry in vault_path.iterdir():
        if entry.suffix in (".so", ".pyd") and entry.stem.split(".")[0] == name:
            return entry
    return None


def commit(
    *,
    name: str,
    version: str,
    wheel_tag: str,
    python_tag: str,
    abi_tag: str,
    platform_tag: str,
    staged: Path,
    source: str,
    sha256: Optional[str] = None,
    metadata: Optional[dict] = None,
    overwrite: bool = False,
) -> Path:
    """Move a fully-prepared staging dir into the vault and index it.

    Atomicity: the move is a single os.rename within the same filesystem.
    On failure, the staging dir is left for inspection.
    """
    final = vault_path_for(name, version, wheel_tag)
    if final.exists():
        if not overwrite:
            shutil.rmtree(staged, ignore_errors=True)
            return final
        shutil.rmtree(final)
    final.parent.mkdir(parents=True, exist_ok=True)

    # Cross-fs safety: rename if same fs, else copy+remove
    try:
        os.rename(staged, final)
    except OSError:
        shutil.copytree(staged, final, symlinks=True)
        shutil.rmtree(staged, ignore_errors=True)

    has_native = _detect_native(final)
    now = datetime.now().isoformat()
    conn = db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO packages "
        "(name, version, wheel_tag, python_tag, abi_tag, platform_tag, "
        " sha256, source, cached_at, last_used_at, vault_path, has_native, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (name, version, wheel_tag, python_tag, abi_tag, platform_tag,
         sha256, source, now, now, str(final), int(has_native),
         json.dumps(metadata or {})),
    )

    # Index top-level importable names — the bridge from Python's import space
    # to the vault's PyPI-name space. Try top_level.txt first (verified against
    # the staged tree), fall back to scanning for dirs/.py files at the root.
    # Each row carries a content hash of the subtree it claims, computed once
    # here, so the import→artifact edge is cryptographic, not nominal.
    discovered = _discover_top_levels(final)

    others_by_name: dict[str, list[tuple[str, str, str]]] = {}
    if discovered:
        placeholders = ",".join("?" * len(discovered))
        for imp, pkg, ver, tag in conn.execute(
            f"SELECT import_name, package, version, wheel_tag FROM top_level "
            f"WHERE import_name IN ({placeholders}) "
            f"AND NOT (package=? AND version=? AND wheel_tag=?)",
            (*[d[0] for d in discovered], name, version, wheel_tag),
        ):
            others_by_name.setdefault(imp, []).append((pkg, ver, tag))

    conn.execute(
        "DELETE FROM top_level WHERE package=? AND version=? AND wheel_tag=?",
        (name, version, wheel_tag),
    )
    for top_name, top_sha in discovered:
        contenders = others_by_name.get(top_name, [])
        if contenders:
            top_level_contentions.append({
                "import_name": top_name,
                "incoming": (name, version, wheel_tag),
                "incoming_sha256": top_sha,
                "existing": contenders,
            })
        conn.execute(
            "INSERT INTO top_level "
            "(package, version, wheel_tag, import_name, import_sha256) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, version, wheel_tag, top_name, top_sha),
        )

    conn.commit()
    conn.close()
    return final


def _discover_top_levels(vault_path: Path) -> list[tuple[str, str]]:
    """Find the top-level importable names this package contributes, paired
    with a content hash of each subtree.

    Verify mode: a name asserted by `top_level.txt` is recorded only if it
    resolves to a real subpath in the staged tree. An asserted name with no
    corresponding directory or file is silently dropped — we'd rather
    under-claim than record a binding the bytes can't honor.
    """
    asserted: set[str] = set()
    for tl_file in vault_path.rglob("top_level.txt"):
        for line in tl_file.read_text(errors="replace").splitlines():
            n = line.strip()
            if n and not n.startswith("#"):
                asserted.add(n.split("/")[-1])

    cache: dict[Path, str] = {}
    def digest(p: Path) -> str:
        if p not in cache:
            cache[p] = _hash_subtree(p)
        return cache[p]

    if asserted:
        verified: list[tuple[str, str]] = []
        for n in sorted(asserted):
            sub = _top_level_subpath(vault_path, n)
            if sub is not None:
                verified.append((n, digest(sub)))
        if verified:
            return verified

    # Fallback: scan the vault dir's top-level entries.
    found: list[tuple[str, str]] = []
    for entry in sorted(vault_path.iterdir()):
        if entry.name.endswith(".dist-info") or entry.name.endswith(".data"):
            continue
        if entry.is_dir() and (entry / "__init__.py").exists():
            found.append((entry.name, digest(entry)))
        elif entry.suffix == ".py" and entry.stem != "__init__":
            found.append((entry.stem, digest(entry)))
        elif entry.suffix in (".so", ".pyd"):
            # foo.cpython-313-aarch64-linux-gnu.so → foo
            stem = entry.stem.split(".")[0]
            found.append((stem, digest(entry)))
    return found


def remove(name: str, version: str, wheel_tag: str) -> bool:
    """Delete a vault entry and its tree."""
    conn = db.connect()
    row = conn.execute(
        "SELECT vault_path FROM packages WHERE name=? AND version=? AND wheel_tag=?",
        (name, version, wheel_tag),
    ).fetchone()
    if not row:
        conn.close()
        return False
    path = Path(row[0])
    conn.execute(
        "DELETE FROM packages WHERE name=? AND version=? AND wheel_tag=?",
        (name, version, wheel_tag),
    )
    conn.commit()
    conn.close()
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
    return True


def list_all(conn: Optional[sqlite3.Connection] = None) -> list[tuple]:
    """Return rows from packages table."""
    own = conn is None
    if own:
        conn = db.connect()
    rows = list(conn.execute(
        "SELECT name, version, wheel_tag, has_native, source, cached_at, vault_path "
        "FROM packages ORDER BY name, version"
    ))
    if own:
        conn.close()
    return rows


def touch(name: str, version: str, wheel_tag: str) -> None:
    """Update last_used_at — for GC."""
    conn = db.connect()
    conn.execute(
        "UPDATE packages SET last_used_at=? WHERE name=? AND version=? AND wheel_tag=?",
        (datetime.now().isoformat(), name, version, wheel_tag),
    )
    conn.commit()
    conn.close()
