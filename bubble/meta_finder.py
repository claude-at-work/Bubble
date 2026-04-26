"""Demand-paged Python imports backed by the bubble vault.

A MetaPathFinder that intercepts top-level import misses and resolves them
out of the vault's content store. Stdlib short-circuits before we run.

Plus alias support — multi-version coexistence in one process. The script
writes `import click_old` and `import click_new`; the finder serves each from
a different vault dir under a different sys.modules key.
"""

from __future__ import annotations

import importlib.abc
import importlib.machinery
import importlib.util
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional


_STDLIB = getattr(sys, "stdlib_module_names", frozenset())
_MYPYC_RE = re.compile(r"^[0-9a-f]+__mypyc$")
_NAMESPACE_ROOTS = frozenset({"backports", "google", "zope", "ruamel"})


def _untrappable(name: str) -> bool:
    if not name:
        return True
    if name.startswith("_"):
        return True
    if _MYPYC_RE.match(name):
        return True
    if name in _NAMESPACE_ROOTS:
        return True
    return False


class VaultFinder(importlib.abc.MetaPathFinder):
    """Translate a top-level import → vault dir → standard PathFinder.

    Once we hand a vault path to PathFinder, Python's normal machinery handles
    everything. We just answer 'where is the top-level package?'.
    """

    def __init__(
        self,
        *,
        scope: Optional[dict[str, tuple[str, str]]] = None,
        aliases: Optional[dict[str, tuple[str, str, str]]] = None,
        autofetch: bool = False,
        verbose: bool = False,
    ) -> None:
        # scope: {pkg_name: (version, wheel_tag)} — pin versions per package.
        # aliases: {alias_name: (real_name, version, wheel_tag)} — multi-version.
        self._scope = scope
        self._aliases = aliases or {}
        self._autofetch = autofetch
        self._verbose = verbose
        self._hit_log: list[tuple[str, str, str, str]] = []
        self._fetch_failed: set[str] = set()

    @property
    def hits(self) -> list[tuple[str, str, str, str]]:
        return list(self._hit_log)

    def find_spec(self, fullname, path, target=None):
        top = fullname.split(".", 1)[0]

        # Submodule of an alias: e.g. `click_old.testing`.
        if "." in fullname and top in self._aliases:
            real_name, version, wheel_tag = self._aliases[top]
            vault_path = self._alias_vault_path(real_name, version, wheel_tag)
            if vault_path is None:
                return None
            pkg_root = vault_path / real_name
            sub = fullname.split(".", 1)[1]
            spec = importlib.machinery.PathFinder.find_spec(
                fullname, [str(pkg_root)], target,
            )
            if spec is not None and spec.loader is not None:
                spec.loader = _SubAliasLoader(spec.loader, top, real_name)
            return spec

        if "." in fullname:
            return None

        if top in _STDLIB:
            return None
        if top in sys.builtin_module_names:
            return None

        if top in self._aliases:
            return self._spec_for_alias(top)

        if _untrappable(top):
            return None
        if top in self._fetch_failed:
            return None

        vault_path = self._lookup(top)
        if vault_path is None:
            if self._autofetch:
                vault_path = self._fault_to_pypi(top)
            if vault_path is None:
                return None

        spec = importlib.machinery.PathFinder.find_spec(top, [str(vault_path)], target)
        if spec is not None and self._verbose:
            sys.stderr.write(f"[bubble] {fullname} → {vault_path}\n")
        return spec

    # ───────────────────── alias path ─────────────────────

    def _alias_vault_path(self, real_name, version, wheel_tag) -> Optional[Path]:
        from . import config
        if not config.VAULT_DB.exists():
            return None
        try:
            conn = sqlite3.connect(str(config.VAULT_DB))
        except sqlite3.Error:
            return None
        try:
            row = conn.execute(
                "SELECT vault_path FROM packages WHERE name=? AND version=? AND wheel_tag=?",
                (real_name, version, wheel_tag),
            ).fetchone()
        finally:
            conn.close()
        return Path(row[0]) if row else None

    def _spec_for_alias(self, alias: str):
        real_name, version, wheel_tag = self._aliases[alias]
        vault_path = self._alias_vault_path(real_name, version, wheel_tag)
        if vault_path is None:
            return None
        pkg_dir = vault_path / real_name
        init = pkg_dir / "__init__.py"
        if not init.exists():
            return None
        if self._verbose:
            sys.stderr.write(
                f"[bubble] alias {alias} → {real_name}=={version} [{wheel_tag}]\n"
            )
        inner = importlib.machinery.SourceFileLoader(alias, str(init))
        spec = importlib.util.spec_from_file_location(
            alias, str(init),
            loader=_AliasLoader(inner, alias, real_name),
            submodule_search_locations=[str(pkg_dir)],
        )
        return spec

    # ───────────────────── default path ─────────────────────

    def _lookup(self, name: str) -> Optional[Path]:
        from . import config
        from .vault import metadata as meta
        if not config.VAULT_DB.exists():
            return None
        try:
            conn = sqlite3.connect(str(config.VAULT_DB))
        except sqlite3.Error:
            return None
        try:
            row = self._query_vault(conn, name, meta.normalize_name(name))
        finally:
            conn.close()
        if row is None:
            return None
        pkg_name, version, wheel_tag, vault_path = row
        self._hit_log.append((name, pkg_name, version, wheel_tag))
        return Path(vault_path)

    def _query_vault(self, conn, import_name, normalized):
        rows = list(conn.execute(
            "SELECT p.name, p.version, p.wheel_tag, p.vault_path "
            "FROM packages p JOIN top_level t "
            "  ON p.name=t.package AND p.version=t.version AND p.wheel_tag=t.wheel_tag "
            "WHERE t.import_name = ?",
            (import_name,),
        ))
        if not rows:
            rows = list(conn.execute(
                "SELECT name, version, wheel_tag, vault_path FROM packages "
                "WHERE name=? OR LOWER(REPLACE(REPLACE(name,'_','-'),'.','-')) = ?",
                (import_name, normalized),
            ))
        if not rows:
            return None
        if self._scope:
            # Scope is a *pin* — for packages listed here, only allow the
            # specified (version, tag). For packages NOT listed, fall through
            # to default best-pick. This lets users pin what they care about
            # without enumerating every transitive dep.
            from .vault import metadata as _meta
            scope_norm = {_meta.normalize_name(k): v for k, v in self._scope.items()}
            pkg_norm_set = {_meta.normalize_name(r[0]) for r in rows}
            scoped_pkgs = pkg_norm_set & set(scope_norm)
            if scoped_pkgs:
                # At least one matching package is pinned — filter to the pin
                allowed = []
                for name, version, tag, path in rows:
                    spec = scope_norm.get(_meta.normalize_name(name))
                    if spec and (version, tag) == spec:
                        allowed.append((name, version, tag, path))
                if not allowed:
                    return None
                rows = allowed
            # else: no pin for this package; fall through to free pick
        from .run.shell import _version_key, _wheel_tag_score
        rows.sort(key=lambda r: (_version_key(r[1]), _wheel_tag_score(r[2])),
                  reverse=True)
        return rows[0]

    def _fault_to_pypi(self, name: str) -> Optional[Path]:
        from .scanner.py import IMPORT_TO_DIST
        from . import host
        dist = IMPORT_TO_DIST.get(name, name)
        if self._verbose:
            extra = f" (dist={dist})" if dist != name else ""
            sys.stderr.write(f"[bubble] vault miss for {name!r}, fetching from PyPI{extra}…\n")
        try:
            from .vault import fetcher
            result = fetcher.fetch_into_vault(dist)
        except Exception as exc:
            if self._verbose:
                sys.stderr.write(f"[bubble] fetch failed: {exc}\n")
            self._fetch_failed.add(name)
            host.record_failure("pypi_fetch_failed", dist,
                                f"{type(exc).__name__}: {exc}")
            return None
        if not result:
            self._fetch_failed.add(name)
            host.record_failure("pypi_no_compatible_release", dist,
                                f"import_name={name}")
            return None
        return self._lookup(name)


class _AliasLoader(importlib.abc.Loader):
    """Load a package as `<alias>` while its internal `from <real_name> import x`
    statements continue to work — by temporarily binding `<real_name>` to the
    alias module in sys.modules during exec.
    """

    def __init__(self, inner, alias: str, real_name: str):
        self._inner = inner
        self._alias = alias
        self._real = real_name

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        saved = sys.modules.pop(self._real, None)
        saved_subs = {k: sys.modules.pop(k) for k in list(sys.modules)
                      if k.startswith(f"{self._real}.")}
        sys.modules[self._real] = module
        try:
            self._inner.exec_module(module)
            # Re-key newly-loaded <real>.* into <alias>.*
            for k in list(sys.modules):
                if k == self._real or k.startswith(f"{self._real}."):
                    aliased = k.replace(self._real, self._alias, 1)
                    if aliased not in sys.modules:
                        sys.modules[aliased] = sys.modules[k]
        finally:
            for k in list(sys.modules):
                if k == self._real or k.startswith(f"{self._real}."):
                    if k != self._real or saved is None:
                        del sys.modules[k]
            if saved is not None:
                sys.modules[self._real] = saved
            for k, v in saved_subs.items():
                sys.modules[k] = v


class _SubAliasLoader(importlib.abc.Loader):
    """For submodules of an alias: e.g. loading `click_old.testing`.

    The submodule's source has internal `from click import x`, which must
    resolve to `click_old`, not whatever default `click` is in sys.modules.
    Same swap-and-restore trick as _AliasLoader, scoped to a submodule.
    """

    def __init__(self, inner, alias_top: str, real_name: str):
        self._inner = inner
        self._alias_top = alias_top
        self._real = real_name

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        # Temporarily expose alias_top as real_name so internal absolute
        # imports of the real package name resolve to the aliased copy.
        saved = sys.modules.get(self._real)
        if self._alias_top in sys.modules:
            sys.modules[self._real] = sys.modules[self._alias_top]
        try:
            self._inner.exec_module(module)
        finally:
            if saved is not None:
                sys.modules[self._real] = saved
            elif self._real in sys.modules:
                del sys.modules[self._real]


def install(
    *,
    scope: Optional[dict[str, tuple[str, str]]] = None,
    aliases: Optional[dict[str, tuple[str, str, str]]] = None,
    autofetch: bool = False,
    verbose: bool = False,
) -> VaultFinder:
    """Register the finder on sys.meta_path. Aliases need front-of-list."""
    finder = VaultFinder(scope=scope, aliases=aliases, autofetch=autofetch, verbose=verbose)
    if aliases:
        sys.meta_path.insert(0, finder)
    else:
        sys.meta_path.append(finder)
    return finder


def install_from_env() -> Optional[VaultFinder]:
    if not os.environ.get("BUBBLE_AUTOFAULT"):
        return None
    scope = aliases = None
    scope_path = os.environ.get("BUBBLE_SCOPE")
    if scope_path:
        scope = _load_scope(Path(scope_path))
        aliases = _load_aliases(Path(scope_path))
    return install(
        scope=scope or None,
        aliases=aliases or None,
        autofetch=bool(os.environ.get("BUBBLE_AUTOFETCH")),
        verbose=bool(os.environ.get("BUBBLE_VERBOSE")),
    )


# ─────────────────────── manifest parsing ───────────────────────


def _load_scope(path: Path) -> dict[str, tuple[str, str]]:
    return _load_section(path, "packages",
        r'"([^"]+)"\s*=\s*\{\s*version\s*=\s*"([^"]+)"\s*,'
        r'\s*wheel_tag\s*=\s*"([^"]+)"\s*\}',
        lambda g: (g[0], (g[1], g[2])),
    )


def _load_aliases(path: Path) -> dict[str, tuple[str, str, str]]:
    return _load_section(path, "aliases",
        r'([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{\s*name\s*=\s*"([^"]+)"\s*,'
        r'\s*version\s*=\s*"([^"]+)"\s*,'
        r'\s*wheel_tag\s*=\s*"([^"]+)"\s*\}',
        lambda g: (g[0], (g[1], g[2], g[3])),
    )


def _load_section(path: Path, section: str, line_re: str, extract):
    out: dict = {}
    if not path.exists():
        return out
    in_section = False
    target = f"[{section}]"
    pattern = re.compile(line_re)
    for line in path.read_text().splitlines():
        line = line.strip()
        if line == target:
            in_section = True
            continue
        if line.startswith("[") and line != target:
            in_section = False
            continue
        if not in_section or not line or line.startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            k, v = extract(m.groups())
            out[k] = v
    return out
