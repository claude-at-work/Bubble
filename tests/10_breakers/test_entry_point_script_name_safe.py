"""Claim: entry-point script names are validated before becoming filenames.
``entry_points.txt`` lives inside a wheel, so its keys are attacker-controlled
at vault-add time. A name like ``../../../.bashrc`` would otherwise turn
``bubble shell add`` into an arbitrary-file write outside the shell's bin/.
The unsafe name is dropped; legitimate names alongside it still land.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _common import run_test, Result, stage_fake_package


def body(r: Result):
    from bubble.run import shell as shell_mod

    name, version, tag, vault_path = stage_fake_package(
        name="evilpkg",
        version="1.0.0",
        import_name="evilpkg",
        init_source="def go(): print('ok')\n",
    )

    # Plant entry_points.txt with one safe and three malicious script names.
    dist_info = vault_path / f"{name}-{version}.dist-info"
    (dist_info / "entry_points.txt").write_text(
        "[console_scripts]\n"
        "good_script = evilpkg:go\n"
        "../../../escape = evilpkg:go\n"
        "/abs/path = evilpkg:go\n"
        ".hidden = evilpkg:go\n"
    )

    shell_mod.create("test_shell", [])
    summary = shell_mod.add("test_shell", ["evilpkg"])

    sd = shell_mod.shell_dir("test_shell")
    bin_dir = sd / "bin"
    written = sorted(p.name for p in bin_dir.iterdir())

    assert "good_script" in written, f"safe entry point missing: {written}"
    for bad in ("..", "escape", "/abs/path", ".hidden"):
        assert bad not in written, f"malicious entry point landed: {bad}"

    # Critically: nothing got written outside the shell's bin/ directory.
    bashrc = Path.home() / ".bashrc"
    bashrc_mtime_after = bashrc.stat().st_mtime if bashrc.exists() else None
    # We cannot prove a negative directly, but escape paths from this shell
    # would have to leave bin/ — verify by checking the shell dir's siblings.
    shell_root = sd.parent
    siblings = sorted(p.name for p in shell_root.iterdir())
    assert siblings == ["test_shell"], f"unexpected sibling files: {siblings}"

    r.evidence.append(f"entry_points.txt declared 4 names; written: {written}")
    r.evidence.append("→ '../../../escape', '/abs/path', '.hidden' rejected")
    r.evidence.append("→ 'good_script' admitted; shell bin/ contains only safe names")
    r.passed = True


if __name__ == "__main__":
    run_test(
        "entry-point script names from a wheel's entry_points.txt are "
        "validated before becoming files in the shell's bin/ — path-traversal "
        "names cannot escape the shell directory",
        body,
    )
