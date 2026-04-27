"""Parse package metadata: METADATA (PKG-INFO format) and WHEEL files."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


# Names that flow into PyPI URL construction must be PEP 503-shaped: a letter
# or digit followed by letters, digits, ``.``, ``_``, ``-``. Validating at the
# trust boundary closes the gap where a name captured from a dynamic-import
# error string or an attacker-controlled __init__.py would otherwise reach
# urllib unchecked. Cap at 128 chars; PyPI's longest published name is ~60.
_SAFE_DIST_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$")


def is_safe_dist_name(name: str) -> bool:
    """True iff `name` is shaped like a legal PEP 503 distribution name.
    Used as a pre-fetch trust gate so URL/path construction never sees junk."""
    return bool(_SAFE_DIST_NAME.match(name))


def parse_metadata(text: str) -> dict:
    """Parse a PKG-INFO / METADATA RFC822-ish file.

    Returns dict with: name, version, requires_dist (list[str]), summary,
    requires_python, and any other Single-line headers we encounter.
    Multi-line bodies (the long description) are dropped — header section only.
    """
    headers: dict = {"requires_dist": []}
    for raw_line in text.splitlines():
        # Headers end at first blank line
        if not raw_line.strip():
            break
        if raw_line[:1] in (" ", "\t"):
            # continuation of previous header — skip for our needs
            continue
        if ":" not in raw_line:
            continue
        key, _, value = raw_line.partition(":")
        key = key.strip().lower().replace("-", "_")
        value = value.strip()
        if key == "requires_dist":
            headers["requires_dist"].append(value)
        else:
            # Last value wins for repeated single-line headers (rare)
            headers[key] = value
    return headers


def parse_wheel_file(text: str) -> dict:
    """Parse a WHEEL file (in a .dist-info directory). Returns dict of fields."""
    out: dict = {"tag": []}
    for raw in text.splitlines():
        if not raw.strip() or ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key = key.strip().lower().replace("-", "_")
        val = val.strip()
        if key == "tag":
            out["tag"].append(val)
        else:
            out[key] = val
    return out


def derive_wheel_tag_from_dist_info(dist_info_dir: Path) -> tuple[str, str, str, str]:
    """Look at <dist-info>/WHEEL and return (full_tag, py, abi, plat).

    Returns ('py3-none-any', 'py3', 'none', 'any') as a default if WHEEL is
    missing or unreadable. Wheels have one or more Tag: lines like
    'cp313-cp313-manylinux2014_aarch64'. Pick the first.
    """
    wheel_path = dist_info_dir / "WHEEL"
    if not wheel_path.exists():
        return ("py3-none-any", "py3", "none", "any")
    try:
        info = parse_wheel_file(wheel_path.read_text(errors="replace"))
    except OSError:
        return ("py3-none-any", "py3", "none", "any")
    tags = info.get("tag", [])
    if not tags:
        return ("py3-none-any", "py3", "none", "any")
    first = tags[0]
    parts = first.split("-")
    if len(parts) == 3:
        return (first, parts[0], parts[1], parts[2])
    return (first, "py3", "none", "any")


def name_version_from_dist_info(dist_info_dir: Path) -> Optional[tuple[str, str]]:
    """Read METADATA from a *.dist-info dir and return (name, version)."""
    md = dist_info_dir / "METADATA"
    if not md.exists():
        return None
    try:
        h = parse_metadata(md.read_text(errors="replace"))
    except OSError:
        return None
    name = h.get("name")
    version = h.get("version")
    if not name or not version:
        return None
    return (name, version)


def normalize_name(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()
