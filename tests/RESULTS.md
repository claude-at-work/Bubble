# Bubble — test gallery

Each entry below is an architectural claim that was tested by running real code against the actual `bubble` package on this machine. The tests are also exhibits — read them to learn what the system does.

_Run: 2026-04-27T08:25:36 — 11 passed, 0 failed, 0 skipped._

---

## ✓ a fresh BUBBLE_HOME yields a usable vault DB on schema v2

`00_sanity/test_vault_initializes.py` — 15 ms

```
vault_db: /tmp/bubble-test-75wcfihw/vault.db
tables: 8 (bubbles, dependencies, module_imports, modules, packages, schema_meta, shells, top_level)
packages PK: ['name', 'version', 'wheel_tag']
```

## ✓ a stdlib-only run with autofetch on leaves the vault empty

`10_breakers/test_empty_script_fetches_nothing.py` — 22 ms

```
stdlib imports: 10
vault.packages rows: 0
wheels/ entries:    0
→ demand paging: zero touched, zero paid
```

## ✓ fetcher refuses non-allowlisted download URLs (off-host, http, file://) before any network work — a poisoned simple-API response can't redirect us

`10_breakers/test_fetcher_refuses_off_host_url.py` — 28 ms

```
http://files.pythonhosted.org/...           → rejected
https://evil.example.com/wheel.whl          → rejected
file:///etc/passwd                          → rejected
https://files.pythonhosted.org/...          → admitted
→ poisoned-mirror redirects fail before any bytes are fetched
```

## ✓ a late-arriving alias does not retroactively corrupt earlier imports — Bubble's isolation is temporal, not just spatial

`10_breakers/test_late_alias_does_not_corrupt_earlier.py` — 35 ms

```
t0: widget_old.VERSION=1.0.0, hello='v1 says hi', calls=1
t1: widget_new arrives. VERSION=2.0.0, hello='v2 says hi'
t2: re-using widget_old. VERSION=1.0.0, hello='v1 says hi', calls=2
module id stable:  0x7f905f044d10 → 0x7f905f044d10
class id stable:   0x35e97d90 → 0x35e97d90
STATE dicts distinct: old@0x7f905f03b9c0 vs new@0x7f905f03bd00
→ time axis: a late alias did not contaminate earlier state
```

## ✓ every top_level row carries a content sha256 over its subtree, populated at vault-add — the import-name → bytes edge is cryptographic

`10_breakers/test_top_level_carries_content_hash.py` — 30 ms

```
alpha import_sha256: 8a94beb727299d2f180df11e817b2858ca6f7478a1011c0a7ec543fa9368a4e1
beta  import_sha256: 7eef7974f5e08293d88af8537fb8f37b11d081b3c7cb35a0b1ccc817c882edcd
→ each top_level row binds the import name to its bytes
```

## ✓ import-name collisions across distributions emit a structured contention log entry — silent accident becomes observable event

`10_breakers/test_top_level_contention_logged.py` — 33 ms

```
first claimant:  opencv-python (no log)
second claimant: opencv-python-headless
contention recorded: import_name=cv2
existing: [('opencv-python', '4.10.0', 'py3-none-any')]
incoming sha256: 66e79245b348a9ba…
→ collisions are observable, not silent
```

## ✓ import name resolves to a different distribution name via the SQLite top_level index, with no hardcoded table

`10_breakers/test_top_level_index_bridges_import_to_dist.py` — 27 ms

```
distribution name: Carbohydrate-9000
top-level import:  sugar
top_level row:     ('Carbohydrate-9000', '3.0.0', 'sugar')
resolved module:   /tmp/bubble-test-yijyk3my/vault/Carbohydrate-9000/3.0.0/py3-none-any/sugar/__init__.py
→ no hardcoded mapping needed; the dist-info IS the mapping
```

## ✓ top_level.txt is verified against the staged tree — asserted-but-absent names are dropped, so no row claims bytes that don't exist

`10_breakers/test_top_level_verify_mode.py` — 27 ms

```
top_level.txt asserted: ['real', 'ghost']
verified subpaths exist for: ['real']
recorded in top_level: ['real']
→ wheel self-attestation is verified, not trusted
```

## ✓ two versions of the same package coexist in one process via aliases, with distinct classes and asymmetric isinstance

`10_breakers/test_two_versions_one_process.py` — 29 ms

```
widget_old.Widget: id=0x3ee34d0
widget_new.Widget: id=0x3ee3bb0
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

`30_loop/test_probe_writes_host_toml.py` — 16 ms

```
probed_at: 2026-04-27T08:25:36.606875
kernel:    Linux 6.18.5 x86_64
python:    3.11.15 (cpython)
substrates this machine reports it can host:
  - in_process         available                                        cost=0MB
  - sub_interpreter    unavailable                                      cost=1MB
  - dlmopen_isolated   available (multi-call needs GIL-managed re-entry) cost=7MB
  - subprocess         available                                        cost=30MB
→ probe writes, host reads, the portrait is real
```
