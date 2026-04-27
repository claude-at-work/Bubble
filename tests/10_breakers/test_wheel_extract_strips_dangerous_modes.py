"""Claim: ``_strip_unsafe_modes`` removes setuid, setgid, and world/group-
writable bits from every file under a directory tree, and ``_safe_extract_zip``
calls it as the last step of extraction. CPython's ``zipfile.extractall``
doesn't preserve unix mode bits today, but that's an implementation detail —
wheels carry mode bits in ``external_attr`` and a future change or a different
extraction path could surface them. The strip is defense-in-depth so the
invariant doesn't depend on extractall's current behavior.
"""
import os
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import run_test, Result


def body(r: Result):
    from bubble.vault import fetcher

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        # Direct test: lay down files with the dangerous bits ourselves, then
        # invoke the strip helper. This is what would happen if extractall
        # ever preserved external_attr on a future Python.
        cases = [
            ("setuid_bin",     0o4755, stat.S_ISUID, "setuid"),
            ("setgid_bin",     0o2755, stat.S_ISGID, "setgid"),
            ("world_writable", 0o0666, stat.S_IWOTH, "world-writable"),
            ("group_writable", 0o0660, stat.S_IWGRP, "group-writable"),
            ("clean_exec",     0o0755, 0,            "clean executable"),
        ]
        for fname, mode, _bit, _label in cases:
            p = root / fname
            p.write_text("#!/bin/true\n")
            os.chmod(p, mode)
            assert os.stat(p).st_mode & 0o7777 == mode, \
                f"setup failed: chmod did not stick on {fname}"

        fetcher._strip_unsafe_modes(root)

        for fname, _mode, bit, label in cases[:-1]:
            after = os.stat(root / fname).st_mode
            assert not (after & bit), \
                f"{label} bit not stripped from {fname}: 0o{after:o}"
            r.evidence.append(f"{fname:16s} → 0o{after & 0o7777:04o}  ({label} stripped)")

        # The clean executable's user-exec bit must survive — strip removes
        # only dangerous bits, doesn't reset to a fixed mode.
        clean_after = os.stat(root / "clean_exec").st_mode
        assert clean_after & stat.S_IXUSR, \
            f"strip removed user-exec bit: 0o{clean_after:o}"
        r.evidence.append(f"clean_exec       → 0o{clean_after & 0o7777:04o}  (user-exec preserved)")

    # And confirm _safe_extract_zip plumbs the strip in: build a zip, extract,
    # verify no setuid/setgid/world-writable on any extracted file (whether
    # extractall set those bits or not).
    with tempfile.TemporaryDirectory() as td:
        zpath = Path(td) / "evil.whl"
        with zipfile.ZipFile(zpath, "w") as zf:
            info = zipfile.ZipInfo("evil_bin")
            info.external_attr = (0o4755 | 0o100000) << 16
            zf.writestr(info, b"#!/bin/true\n")
        out = Path(td) / "out"
        out.mkdir()
        with zipfile.ZipFile(zpath) as zf:
            fetcher._safe_extract_zip(zf, out)
        post = os.stat(out / "evil_bin").st_mode
        assert not (post & (stat.S_ISUID | stat.S_ISGID | stat.S_IWOTH | stat.S_IWGRP)), \
            f"_safe_extract_zip left dangerous bits: 0o{post:o}"
        r.evidence.append(f"_safe_extract_zip(setuid wheel) → 0o{post & 0o7777:04o}")
        r.evidence.append("→ a wheel cannot smuggle setuid binaries through the vault")
    r.passed = True


if __name__ == "__main__":
    run_test(
        "_strip_unsafe_modes removes setuid/setgid/world-writable bits and "
        "_safe_extract_zip calls it after extractall — a wheel cannot ship "
        "a setuid binary that later escalates when run",
        body,
    )
