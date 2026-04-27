"""Claim: BUBBLE_PYPI_INDEX must be https. An http index lets a network
attacker rewrite the simple-API JSON, choosing both the wheel URL and its
sha256 — sha verification then succeeds against an attacker-supplied hash.
The download-host allowlist cannot save us if the index itself is cleartext,
so the fetcher refuses to load with an http index.
"""
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import run_test, Result


REPO_ROOT = Path(__file__).resolve().parents[2]


def _import_fetcher_with_index(index_url: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["BUBBLE_PYPI_INDEX"] = index_url
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", "import bubble.vault.fetcher"],
        env=env, capture_output=True, text=True, timeout=20,
    )


def body(r: Result):
    # http on the canonical host — refused at module load.
    proc = _import_fetcher_with_index("http://pypi.org/simple")
    assert proc.returncode != 0, "http index was accepted"
    assert "https" in proc.stderr, f"unexpected stderr: {proc.stderr!r}"
    r.evidence.append("BUBBLE_PYPI_INDEX=http://pypi.org/simple   → import ValueError")

    # http on an attacker host — refused.
    proc = _import_fetcher_with_index("http://evil.example.com/simple")
    assert proc.returncode != 0, "http index on attacker host was accepted"
    assert "https" in proc.stderr, f"unexpected stderr: {proc.stderr!r}"
    r.evidence.append("BUBBLE_PYPI_INDEX=http://evil.example.com  → import ValueError")

    # file:// — refused.
    proc = _import_fetcher_with_index("file:///tmp/simple")
    assert proc.returncode != 0, "file:// index was accepted"
    assert "https" in proc.stderr, f"unexpected stderr: {proc.stderr!r}"
    r.evidence.append("BUBBLE_PYPI_INDEX=file:///tmp/simple        → import ValueError")

    # https on the default host — admitted.
    proc = _import_fetcher_with_index("https://pypi.org/simple")
    assert proc.returncode == 0, f"default https index rejected: {proc.stderr!r}"
    r.evidence.append("BUBBLE_PYPI_INDEX=https://pypi.org/simple   → admitted")
    r.evidence.append("→ a cleartext index can never serve a wheel into the vault")
    r.passed = True


if __name__ == "__main__":
    run_test(
        "fetcher rejects non-https BUBBLE_PYPI_INDEX at module load — a "
        "cleartext index can supply attacker-chosen sha256 values that pass "
        "verification, so the host allowlist on downloads cannot save us",
        body,
    )
