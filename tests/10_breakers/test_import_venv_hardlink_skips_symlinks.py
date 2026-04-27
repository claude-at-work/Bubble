"""Claim: ``bubble vault import-venv --hardlink`` will not follow symlinks
inside the source site-packages. ``os.link`` follows symlinks by default —
a RECORD entry pointing at /etc/shadow would otherwise hardlink that inode
into the vault, exposing root-only content via the vault path. Symlinks are
skipped unconditionally; legitimate regular files alongside them still land.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import run_test, Result


def body(r: Result):
    from bubble.vault import importer, db
    db.init_db()

    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        site_packages = td_path / "site-packages"
        site_packages.mkdir()

        # A real package directory with one regular file and one symlink.
        pkg_dir = site_packages / "fakepkg"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("# fakepkg\n")

        # The "secret" file the symlink would otherwise hardlink to.
        secret = td_path / "secret_target"
        secret.write_text("ROOT-ONLY-CONTENT")
        os.symlink(secret, pkg_dir / "leaked.py")
        assert (pkg_dir / "leaked.py").is_symlink()

        # Build a dist-info with METADATA, WHEEL, RECORD.
        dist_info = site_packages / "fakepkg-1.0.0.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: fakepkg\nVersion: 1.0.0\n\n"
        )
        (dist_info / "WHEEL").write_text(
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n"
            "Tag: py3-none-any\n"
        )
        (dist_info / "RECORD").write_text(
            "fakepkg/__init__.py,sha256=,12\n"
            "fakepkg/leaked.py,sha256=,17\n"
        )

        result = importer.import_dist_info(dist_info, hardlink=True)
        assert result is not None, "import returned None"
        name, version, tag, copied = result
        assert (name, version) == ("fakepkg", "1.0.0"), result

        # Walk the vault tree — neither a copy nor a hardlink to secret may exist.
        from bubble import config
        vault_root = config.VAULT_DIR
        secret_inode = secret.stat().st_ino
        for root, _dirs, files in os.walk(vault_root):
            for f in files:
                p = Path(root) / f
                # Symlink-following stat: if any vault file shares secret's inode,
                # we hardlinked to it.
                try:
                    st = p.stat()
                except OSError:
                    continue
                assert st.st_ino != secret_inode, \
                    f"hardlinked to secret inode at {p}"
                # And no copied content either:
                if p.name == "leaked.py":
                    body = p.read_text(errors="replace")
                    assert "ROOT-ONLY-CONTENT" not in body, \
                        f"symlink content leaked into vault at {p}"

        r.evidence.append(f"site-packages had: __init__.py (regular) + leaked.py (symlink → secret)")
        r.evidence.append(f"import_dist_info(hardlink=True) → copied={copied}")
        r.evidence.append(f"vault contains no inode-link to {secret} and no leaked.py content")
        r.evidence.append("→ a malicious venv handed to import-venv --hardlink cannot")
        r.evidence.append("  pull root-only files into the vault via symlinks")
    r.passed = True


if __name__ == "__main__":
    run_test(
        "import_dist_info(hardlink=True) skips symlink RECORD entries — "
        "a malicious venv cannot hardlink /etc/shadow into the vault",
        body,
    )
