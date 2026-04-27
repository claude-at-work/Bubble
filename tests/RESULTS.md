# Bubble — test gallery

Each entry below is an architectural claim that was tested by running real code against the actual `bubble` package on this machine. The tests are also exhibits — read them to learn what the system does.

_Run: 2026-04-27T04:22:36 — 7 passed, 0 failed, 0 skipped._

---

## ✓ a fresh BUBBLE_HOME yields a usable vault DB on schema v2

`00_sanity/test_vault_initializes.py` — 14 ms

```
vault_db: /tmp/bubble-test-zv6a_o0j/vault.db
tables: 8 (bubbles, dependencies, module_imports, modules, packages, schema_meta, shells, top_level)
packages PK: ['name', 'version', 'wheel_tag']
```

## ✓ a stdlib-only run with autofetch on leaves the vault empty

`10_breakers/test_empty_script_fetches_nothing.py` — 20 ms

```
stdlib imports: 10
vault.packages rows: 0
wheels/ entries:    0
→ demand paging: zero touched, zero paid
```

## ✓ a late-arriving alias does not retroactively corrupt earlier imports — Bubble's isolation is temporal, not just spatial

`10_breakers/test_late_alias_does_not_corrupt_earlier.py` — 28 ms

```
t0: widget_old.VERSION=1.0.0, hello='v1 says hi', calls=1
t1: widget_new arrives. VERSION=2.0.0, hello='v2 says hi'
t2: re-using widget_old. VERSION=1.0.0, hello='v1 says hi', calls=2
module id stable:  0x7fb0a99316c0 → 0x7fb0a99316c0
class id stable:   0x3c14f00 → 0x3c14f00
STATE dicts distinct: old@0x7fb0a993cc00 vs new@0x7fb0a993cf00
→ time axis: a late alias did not contaminate earlier state
```

## ✓ import name resolves to a different distribution name via the SQLite top_level index, with no hardcoded table

`10_breakers/test_top_level_index_bridges_import_to_dist.py` — 26 ms

```
distribution name: Carbohydrate-9000
top-level import:  sugar
top_level row:     ('Carbohydrate-9000', '3.0.0', 'sugar')
resolved module:   /tmp/bubble-test-9b31geom/vault/Carbohydrate-9000/3.0.0/py3-none-any/sugar/__init__.py
→ no hardcoded mapping needed; the dist-info IS the mapping
```

## ✓ two versions of the same package coexist in one process via aliases, with distinct classes and asymmetric isinstance

`10_breakers/test_two_versions_one_process.py` — 25 ms

```
widget_old.Widget: id=0x1301da10
widget_new.Widget: id=0x1301eae0
widget_old hello:  'I am widget v1'
widget_new hello:  'I am widget v2'
isinstance asymmetric: v1∈v2=False, v2∈v1=False
→ two versions, one process, distinct classes
```

## ✓ runtime failures round-trip through host.toml: write via record_failure, read via known_failures, find via is_known_failure

`30_loop/test_failure_recording_round_trip.py` — 19 ms

```
recorded 3 failures via host.record_failure
distinct kinds: ['dlmopen_unavailable', 'pypi_fetch_failed', 'wheel_load_segfault']
round-tripped detail: 'received SIGSEGV during dlopen'
→ channel is plumbed; record→consult half of the loop works
(open: the *next-run-alters-strategy* half is not yet load-bearing)
```

## ✓ bubble probe writes host.toml; the host module reads it back; the substrate menu reflects machine capability

`30_loop/test_probe_writes_host_toml.py` — 14 ms

```
probed_at: 2026-04-27T04:22:36.083238
kernel:    Linux 6.18.5 x86_64
python:    3.11.15 (cpython)
substrates this machine reports it can host:
  - in_process         available                                        cost=0MB
  - sub_interpreter    unavailable                                      cost=1MB
  - dlmopen_isolated   available (multi-call needs GIL-managed re-entry) cost=7MB
  - subprocess         available                                        cost=30MB
→ probe writes, host reads, the portrait is real
```
