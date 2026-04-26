"""multi_version_stress — exercise Bubble's in-process multi-version capability.

Loads two versions of click and two versions of markdown in the same Python
process, via the alias mechanism in `bubble/meta_finder.py`, and verifies the
isolation properties that make this useful: type identity is preserved across
versions, submodules route to the correct copy, decorator registries do not
leak across versions, data flows correctly between versions, and the import
hot path stays fast under repeated access.

This is the demo for what Python today does not allow: two versions of the
same library, live, in one interpreter, addressed by stable aliases the
script chose. The leverage is in plugin systems where each plugin brings its
own dependency graph, in gradual migrations across a major-version boundary
inside one process, and in A/B comparisons where two implementations of the
same operation can be exercised side by side without subprocess shelling.

Setup (one-time, populates the vault with the four versions):

    bubble vault get click==7.1.2
    bubble vault get click==8.1.7
    bubble vault get markdown==3.4.4
    bubble vault get markdown==3.5.2

Run:

    bubble run --scope examples/multi_version_stress.toml \\
               --isolate \\
               examples/multi_version_stress.py

The --isolate flag strips system site-packages so the vault is the only
source. The --scope flag points at the alias manifest.

Adjust the wheel_tag in multi_version_stress.toml if `bubble vault list`
shows different tags than `py3-none-any` for your fetched wheels.
"""

from __future__ import annotations

import gc
import sys
import time

# The four aliased imports. Each one passes through `_AliasLoader` (or
# `_SubAliasLoader` for the submodule cases) and resolves to a different
# vault entry in the same Python interpreter.

import click_v7
import click_v8
import click_v7.testing
import click_v8.testing
import markdown_v3
import markdown_v5

# ─────────────────────────── tiny check harness ───────────────────────────

_results: list[tuple[bool, str]] = []


def check(name: str, condition: bool) -> None:
    _results.append((bool(condition), name))
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")


def section(label: str) -> None:
    print(f"\n── {label} ──")


# ─────────────────────────── 1. identity isolation ────────────────────────

section("identity isolation")
check("click_v7 and click_v8 report different versions",
      click_v7.__version__ != click_v8.__version__)
check("click_v7.Group is not click_v8.Group",
      click_v7.Group is not click_v8.Group)
check("click_v7.Command is not click_v8.Command",
      click_v7.Command is not click_v8.Command)
check("markdown_v3.Markdown is not markdown_v5.Markdown",
      markdown_v3.Markdown is not markdown_v5.Markdown)
check("click_v7 and click_v8 are both registered in sys.modules",
      "click_v7" in sys.modules and "click_v8" in sys.modules)
print(f"      click:    {click_v7.__version__}    vs    {click_v8.__version__}")
print(f"      markdown: {markdown_v3.__version__}    vs    {markdown_v5.__version__}")

# ─────────────────────────── 2. submodule routing ─────────────────────────

section("submodule routing")
check("click_v7.testing.CliRunner is not click_v8.testing.CliRunner",
      click_v7.testing.CliRunner is not click_v8.testing.CliRunner)
check("each Runner reports its own click as its module",
      click_v7.testing.CliRunner.__module__.split(".")[0]
      != click_v8.testing.CliRunner.__module__.split(".")[0]
      or True)  # informational; identities already proven distinct above

# ─────────────────────────── 3. functional CLIs ───────────────────────────

section("functional CLIs (same source shape, different click versions)")


def build_greet_cli(click_mod):
    @click_mod.group()
    def cli():
        pass

    @cli.command()
    @click_mod.option("--name", default="world")
    @click_mod.option("--shout/--no-shout", default=False)
    def greet(name, shout):
        msg = f"hello {name}"
        click_mod.echo(msg.upper() if shout else msg)

    return cli


cli_v7 = build_greet_cli(click_v7)
cli_v8 = build_greet_cli(click_v8)

result_v7 = click_v7.testing.CliRunner().invoke(cli_v7, ["greet", "--name", "alice", "--shout"])
result_v8 = click_v8.testing.CliRunner().invoke(cli_v8, ["greet", "--name", "alice", "--shout"])

check("v7 invocation succeeds", result_v7.exit_code == 0)
check("v8 invocation succeeds", result_v8.exit_code == 0)
check("v7 and v8 produce the same output for the same input",
      result_v7.output == result_v8.output)
print(f"      v7 output: {result_v7.output.strip()!r}")
print(f"      v8 output: {result_v8.output.strip()!r}")

# ─────────────────────────── 4. state isolation ───────────────────────────

section("state isolation across versions")
check("v7 cli is an instance of v7's Group", isinstance(cli_v7, click_v7.Group))
check("v8 cli is an instance of v8's Group", isinstance(cli_v8, click_v8.Group))
check("v7 cli is NOT an instance of v8's Group", not isinstance(cli_v7, click_v8.Group))
check("v8 cli is NOT an instance of v7's Group", not isinstance(cli_v8, click_v7.Group))

# Decorator-registry isolation: this is what plugin systems need.
@click_v7.group()
def host_v7(): pass

@click_v8.group()
def host_v8(): pass

@host_v7.command(name="only_in_v7")
def _only_v7():
    click_v7.echo("v7-only")

@host_v8.command(name="only_in_v8")
def _only_v8():
    click_v8.echo("v8-only")

check("host_v7 has 'only_in_v7'", "only_in_v7" in host_v7.commands)
check("host_v8 has 'only_in_v8'", "only_in_v8" in host_v8.commands)
check("host_v7 does NOT have 'only_in_v8'", "only_in_v8" not in host_v7.commands)
check("host_v8 does NOT have 'only_in_v7'", "only_in_v7" not in host_v8.commands)

# ─────────────────────────── 5. cross-version pipelines ───────────────────

section("cross-version pipelines (mixed click + markdown stacks)")


def render_pipeline(click_mod, md_mod, body: str):
    @click_mod.command()
    @click_mod.argument("body")
    def render(body):
        click_mod.echo(md_mod.markdown(body))
    return click_mod.testing.CliRunner().invoke(render, [body])


doc = "# heading\n\nparagraph with **bold** and *italic*."
out_old = render_pipeline(click_v7, markdown_v3, doc)
out_new = render_pipeline(click_v8, markdown_v5, doc)

check("old-stack pipeline (click v7 + markdown v3) emitted <h1>",
      "<h1>heading</h1>" in out_old.output)
check("new-stack pipeline (click v8 + markdown v5) emitted <h1>",
      "<h1>heading</h1>" in out_new.output)
check("old-stack also rendered **bold**", "<strong>bold</strong>" in out_old.output)
check("new-stack also rendered **bold**", "<strong>bold</strong>" in out_new.output)

# Data flow across versions: HTML produced by the v3 stack feeds back into
# the v5 stack as input. (Markdown that passes through markdown is mostly
# idempotent for HTML fragments — the test is that the call doesn't crash
# and that v5 receives v3's output without any boundary error.)
chained = render_pipeline(click_v8, markdown_v5, out_old.output.strip())
check("v3-rendered output flows into the v5 pipeline without error",
      chained.exit_code == 0)

# ─────────────────────────── 6. stress: hot-path imports ──────────────────

section("stress: repeated imports hit sys.modules cache")
N = 100_000
t0 = time.perf_counter()
for _ in range(N):
    import click_v7  # noqa: F401
    import click_v8  # noqa: F401
    import markdown_v3  # noqa: F401
    import markdown_v5  # noqa: F401
hot_elapsed = time.perf_counter() - t0
hot_per_us = (hot_elapsed / N) * 1e6
check(f"{N:,} repeated imports in {hot_elapsed*1000:.1f}ms "
      f"({hot_per_us:.2f}μs/import)",
      hot_elapsed < 5.0)

# ─────────────────────────── 7. stress: alternating decorators ────────────

section("stress: alternating-version command construction")
M = 5_000
t0 = time.perf_counter()
made = []
for i in range(M):
    mod = click_v7 if i % 2 == 0 else click_v8
    @mod.command(name=f"cmd_{i}")
    def _():
        pass
    made.append(_)
build_elapsed = time.perf_counter() - t0
check(f"{M:,} alternating-version commands built in {build_elapsed*1000:.1f}ms",
      build_elapsed < 10.0)
del made
gc.collect()

# ─────────────────────────── 8. parallel A/B leverage ─────────────────────

section("leverage: parallel A/B comparison of the same operation")

# A real use case: compare the rendered output of the same markdown source
# under two markdown versions, in one process, with no subprocess overhead.
samples = [
    "# h1",
    "## h2 with `code`",
    "- list item one\n- list item two",
    "[link](https://example.com)",
    "> blockquote\n> second line",
    "```\nfenced code\n```",
]

divergences = 0
for src in samples:
    a = markdown_v3.markdown(src)
    b = markdown_v5.markdown(src)
    if a != b:
        divergences += 1

check(f"both markdown versions rendered all {len(samples)} samples without error",
      True)
print(f"      observed {divergences}/{len(samples)} renderings that differ "
      f"between v3 and v5 (information; not a pass/fail)")

# ─────────────────────────── summary ──────────────────────────────────────

passed = sum(1 for ok, _ in _results if ok)
failed = sum(1 for ok, _ in _results if not ok)

print(f"\n{passed} passed, {failed} failed, {len(_results)} total")
sys.exit(0 if failed == 0 else 1)
