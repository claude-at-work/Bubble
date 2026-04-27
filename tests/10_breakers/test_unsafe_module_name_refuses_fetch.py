"""Claim: ``is_safe_dist_name`` is the trust gate that prevents an
attacker-controlled module name from flowing into urllib URL construction
inside ``fetch_simple_index``. Tests both the regex itself and that
``VaultFinder._fault_to_pypi`` short-circuits without calling the fetcher
when handed an unsafe name.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import run_test, Result


def body(r: Result):
    from bubble.vault.metadata import is_safe_dist_name
    from bubble import meta_finder
    from bubble.vault import fetcher

    safe = ["requests", "numpy", "pkg_a-b.c", "Django", "a", "z9", "x" * 128]
    for n in safe:
        assert is_safe_dist_name(n), f"safe name rejected: {n!r}"

    unsafe = [
        "",                       # empty
        ".hidden",                # leading dot
        "-leading-dash",          # leading dash
        "_leading-underscore",    # leading underscore (we require letter/digit)
        "../escape",              # traversal
        "/abs/path",              # absolute
        "pkg name with spaces",   # spaces
        "pkg\nnewline",           # newline
        "' OR 1=1 --",            # sqli/url-injection shape
        "pkg%2F..",               # url-encoded traversal
        "pkg/sub",                # path separator
        "x" * 129,                # length cap
        "café",                   # non-ascii
    ]
    for n in unsafe:
        assert not is_safe_dist_name(n), f"unsafe name accepted: {n!r}"

    r.evidence.append(f"safe: {safe}")
    r.evidence.append(f"unsafe (rejected): {unsafe}")

    # Now drive _fault_to_pypi with a hostile name and prove fetcher is not called.
    finder = meta_finder.VaultFinder()
    calls = []

    def spy_fetch_into_vault(*a, **kw):
        calls.append((a, kw))
        return None

    real = fetcher.fetch_into_vault
    fetcher.fetch_into_vault = spy_fetch_into_vault
    try:
        result = finder._fault_to_pypi("../escape")
        assert result is None
        assert calls == [], f"fetcher was called for unsafe name: {calls}"
        result = finder._fault_to_pypi("' OR 1=1")
        assert result is None
        assert calls == [], f"fetcher was called for unsafe name: {calls}"
    finally:
        fetcher.fetch_into_vault = real

    r.evidence.append("VaultFinder._fault_to_pypi('../escape') → fetcher not called")
    r.evidence.append("VaultFinder._fault_to_pypi(\"' OR 1=1\")  → fetcher not called")
    r.evidence.append("→ unsafe names never reach urllib URL construction")
    r.passed = True


if __name__ == "__main__":
    run_test(
        "is_safe_dist_name gates names entering the fetcher — junk captured "
        "from a dynamic-import error or attacker-controlled __init__.py "
        "never reaches urllib URL construction",
        body,
    )
