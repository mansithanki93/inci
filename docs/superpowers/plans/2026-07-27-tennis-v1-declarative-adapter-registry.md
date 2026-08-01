# Tennis v1 Declarative Adapter Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Tennis v1's executable adapter-contract factory with a
data-only, code-owned registry that constructs contracts without importing or
executing adapter Python.

**Architecture:** A frozen `_AdapterContractSpec` holds only validated
provider, authentication, quota-usage, and format values. A frozen
`_AdapterRegistration` binds that declaration to exact safely read adapter
file pins. `load_active_adapter_contract` rechecks those pins, computes the
existing provenance digests, and constructs `AdapterContract` entirely inside
trusted Tennis v1 code.

**Tech Stack:** Python 3.12+ standard library (`dataclasses`, `enum`,
`hashlib`, `os`, `pathlib`, `stat`, `unittest`, `unittest.mock`).

## Global Constraints

- Work only in `/Users/mthanki/Downloads/inci-tennis-v1` on
  `feature/tennis-v1-foundation`.
- Use the absolute interpreter named by `INCI_TENNIS_PYTHON`; verify it with
  `tools/verify_runtime.py` before tests.
- Do not modify any protected v6 file.
- `BASE_SNAPSHOT_UNCOMMITTED` remains active. Do not stage or commit any file;
  record per-task before/after hashes in the existing SDD ledger and review
  packages instead.
- Do not add network calls, provider SDKs, provider activation, trial use,
  demo orders, live orders, paid features, or credentials.
- The production `_ADAPTER_REGISTRY` remains empty.
- No adapter Python, import, callable, class, module object, or loaded
  namespace may execute or be traversed during registration capture or
  entitlement contract loading.
- Preserve the existing adapter closure, authentication contract, and quota
  contract digest domains and canonical projections exactly.
- Preserve descriptor-safe file reads, exact closed-file discovery, single-link
  regular-file checks, source-drift rejection, and fail-closed errors.
- Implement with red-green-refactor. Do not weaken or delete a security
  regression merely to make the suite pass.
- Task 3 of the parent entitlement plan remains blocked until this plan passes
  all verification and independent adversarial review.

## File Map

- Modify `tennis_v1/adapter_contract.py`: declarative spec, registration
  capture, closed-file loading, trusted contract construction, and existing
  quota derivation.
- Modify `tests/tennis_v1/fixtures/synthetic_adapter.py`: inert source file
  that raises if qualification accidentally imports or executes it.
- Rewrite `tests/tennis_v1/test_adapter_contract.py`: declarative contract,
  file pinning, deterministic digest, invalid-data, and no-execution
  regressions.
- Modify `tests/tennis_v1/test_entitlements.py`: construct the same synthetic
  registration from static data rather than an imported factory.
- Modify
  `.superpowers/sdd/2026-07-27-tennis-v1-entitlement-gate/progress.md`: record
  the redesign state and final gate.
- Modify
  `.superpowers/sdd/2026-07-27-tennis-v1-entitlement-gate/task-2-report.md`:
  replace the stale `DONE` conclusion with the architecture-reset evidence.
- Create
  `.superpowers/sdd/2026-07-27-tennis-v1-entitlement-gate/task-2-declarative-review-package.md`:
  exact review scope, hashes, commands, and adversarial acceptance criteria.

---

### Task 1: Replace the executable adapter boundary end to end

**Files:**

- Modify: `tests/tennis_v1/fixtures/synthetic_adapter.py`
- Modify: `tests/tennis_v1/test_adapter_contract.py`
- Modify: `tests/tennis_v1/test_entitlements.py`

**Interfaces:**

- Consumes the current public `AdapterContract`, `AdapterUsagePlan`,
  `AuthContract`, `AuthMode`, `ProviderQuotas`, `derive_quota_demand`, and
  `load_active_adapter_contract` interfaces.
- Defines the expected private interface that Task 2 must produce:

```python
@dataclass(frozen=True, slots=True)
class _AdapterContractSpec:
    provider_id: str
    product_tier: str
    adapter_id: str
    auth: AuthContract
    usage: AdapterUsagePlan
    formats: tuple[str, ...]


def _capture_adapter_registration(
    *,
    module_paths: tuple[str, ...],
    spec: _AdapterContractSpec,
) -> _AdapterRegistration: ...
```

- Produces tests that first fail against the current callable-factory
  implementation for the architectural reason, then a complete declarative
  implementation that makes the focused suites pass.

- [ ] **Step 1: Capture the current test counts and file hashes**

Run:

```bash
test -n "$INCI_TENNIS_PYTHON"
test -x "$INCI_TENNIS_PYTHON"
"$INCI_TENNIS_PYTHON" tools/verify_runtime.py
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_adapter_contract.AdapterContractTests -v
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_entitlements.ProviderManifestTests -v
shasum -a 256 tennis_v1/adapter_contract.py \
  tests/tennis_v1/test_adapter_contract.py \
  tests/tennis_v1/test_entitlements.py \
  tests/tennis_v1/fixtures/synthetic_adapter.py
```

Expected: runtime verification succeeds; the current focused suites pass; four
lowercase SHA-256 values are recorded in the task report before edits.

- [ ] **Step 2: Make the adapter source fixture inert**

Replace the fixture body with:

```python
"""Inert source bytes used to prove qualification never executes an adapter."""

raise RuntimeError(
    "Tennis v1 entitlement qualification executed synthetic adapter source"
)
```

Remove every `import ... synthetic_adapter` from the tests. Address the file
only by:

```python
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
ADAPTER_FILE = FIXTURE_ROOT / "synthetic_adapter.py"
```

- [ ] **Step 3: Define one static synthetic spec helper in each test module**

Add the exact helper, importing `_AdapterContractSpec` from
`tennis_v1.adapter_contract`:

```python
def synthetic_spec(
    *,
    provider_id: str = "synthetic-provider",
    product_tier: str = "trial-v1",
) -> _AdapterContractSpec:
    return _AdapterContractSpec(
        provider_id=provider_id,
        product_tier=product_tier,
        adapter_id="synthetic-read-only-v1",
        auth=AuthContract(
            mode=AuthMode.API_KEY,
            credential_env_names=("SYNTHETIC_API_KEY",),
        ),
        usage=AdapterUsagePlan(
            startup_requests_fixed=1,
            startup_requests_per_match=2,
            steady_requests_per_minute_fixed=1,
            steady_requests_per_minute_per_match=1,
            resync_requests_per_match=1,
            max_resyncs_per_match_per_hour=2,
            max_connections=1,
            subscriptions_per_match=1,
        ),
        formats=("rest_json", "websocket_json"),
    )
```

Update fixture registration to:

```python
with mock.patch.object(
    adapter_contract,
    "__file__",
    str(ADAPTER_FILE),
):
    registration = _capture_adapter_registration(
        module_paths=("synthetic_adapter.py",),
        spec=synthetic_spec(),
    )
```

- [ ] **Step 4: Replace factory-behavior tests with declarative-boundary tests**

Keep the existing quota arithmetic tests. Replace factory, import allowlist,
loaded-object mutation, reflection, and namespace-escape tests with these
named behaviors:

```python
def test_production_registry_is_empty_and_unknown_provider_fails_closed(self):
    self.assertEqual(adapter_contract._ADAPTER_REGISTRY, {})
    with self.assertRaises(AdapterContractError):
        load_active_adapter_contract(
            provider_id="missing",
            product_tier="trial",
        )


def test_registration_and_load_do_not_import_or_execute_adapter_source(self):
    module_keys = set(sys.modules)
    first = self.load_synthetic()
    second = self.load_synthetic()
    self.assertEqual(first, second)
    self.assertEqual(set(sys.modules), module_keys)
    self.assertEqual(first.adapter_id, "synthetic-read-only-v1")


def test_registration_rejects_callable_module_and_mutable_spec_values(self):
    invalid = (
        lambda: None,
        ModuleType("synthetic_invalid"),
        {},
        SimpleNamespace(provider_id="synthetic-provider"),
    )
    for value in invalid:
        with self.subTest(value=type(value).__name__):
            with self.assertRaises(AdapterContractError):
                self.capture(value)


def test_registry_identity_mismatch_fails_closed(self):
    registration = self.capture(
        synthetic_spec(provider_id="different-provider")
    )
    with self.registered(registration):
        with self.assertRaisesRegex(
            AdapterContractError,
            "adapter contract identity mismatch",
        ):
            load_active_adapter_contract(
                provider_id="synthetic-provider",
                product_tier="trial-v1",
            )


def test_source_change_after_registration_fails_before_any_execution(self):
    registration = self.capture(synthetic_spec())
    ADAPTER_FILE.write_bytes(
        ADAPTER_FILE.read_bytes() + b"\n# changed after registration\n"
    )
    self.addCleanup(
        ADAPTER_FILE.write_bytes,
        ADAPTER_FILE.read_bytes().removesuffix(
            b"\n# changed after registration\n"
        ),
    )
    with self.registered(registration):
        with self.assertRaisesRegex(
            AdapterContractError,
            "active adapter files differ",
        ):
            load_active_adapter_contract(
                provider_id="synthetic-provider",
                product_tier="trial-v1",
            )
```

Implement `capture` and `registered` as test-only helpers using
`mock.patch.object(adapter_contract, "__file__", str(ADAPTER_FILE))` and
`mock.patch.dict(adapter_contract._ADAPTER_REGISTRY, ..., clear=True)`.
For source-drift tests, prefer a temporary copied adapter directory so a failed
test cannot leave the repository fixture modified.

Retain or rewrite the following existing security coverage under the new
model:

- exact closure digest formula;
- repeated deterministic load;
- missing file, symlink, hard link, duplicate path, traversal, and absolute
  path rejection;
- unexpected nested or sibling `.py` rejection;
- source byte drift after capture;
- declaration identity mismatch;
- invalid auth, usage, and formats;
- quota-demand arithmetic and overflow boundaries.

- [ ] **Step 5: Add exact-type and immutable-data regressions**

Add:

```python
def test_spec_requires_exact_frozen_contract_value_types(self):
    invalid_specs = (
        object(),
        {"provider_id": "synthetic-provider"},
        replace(synthetic_spec(), auth=object()),
        replace(synthetic_spec(), usage=object()),
        replace(synthetic_spec(), formats=["rest_json"]),
    )
    for value in invalid_specs:
        with self.subTest(value=repr(type(value))):
            with self.assertRaises(AdapterContractError):
                self.capture(value)


def test_loaded_contract_and_nested_declarations_are_frozen(self):
    loaded = self.load_synthetic()
    with self.assertRaises(FrozenInstanceError):
        loaded.adapter_id = "changed"
    with self.assertRaises(FrozenInstanceError):
        loaded.auth.mode = AuthMode.PUBLIC
    with self.assertRaises(FrozenInstanceError):
        loaded.usage.max_connections = 2
```

Also cover exact built-in strings, exact tuples, sorted unique environment
names, sorted unique supported formats, Boolean rejection for integer quota
fields, and nonempty positive connection/subscription limits.

- [ ] **Step 6: Run the new tests and record RED**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_adapter_contract.AdapterContractTests -v
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_entitlements.ProviderManifestTests -v
```

Expected: import or call-signature failures referencing the missing
`_AdapterContractSpec` or the obsolete `factory` API. Record the exact failure
in `task-2-report.md`. Do not implement production code until this RED proves
the architectural test is active.

- [ ] **Step 7: Record the uncommitted RED checkpoint**

Record the modified test/fixture hashes, command, test count, and expected
failure in the Task 2 report. Do not stage or commit.

---

#### Implementation phase: Build the registry and remove the execution surface

**Files:**

- Modify: `tennis_v1/adapter_contract.py`
- Test: `tests/tennis_v1/test_adapter_contract.py`
- Test: `tests/tennis_v1/test_entitlements.py`

**Interfaces:**

- Consumes the exact `_AdapterContractSpec` and
  `_capture_adapter_registration` signatures from Task 1.
- Preserves:

```python
def load_active_adapter_contract(
    *,
    provider_id: str,
    product_tier: str,
) -> AdapterContract: ...


def derive_quota_demand(...) -> ProviderQuotaDemand: ...
```

- Produces a code-owned `AdapterContract` without invoking adapter-controlled
  Python.

- [ ] **Step 1: Add the frozen declarative types**

Replace `_AdapterRegistration` with:

```python
@dataclass(frozen=True, slots=True)
class _AdapterContractSpec:
    provider_id: str
    product_tier: str
    adapter_id: str
    auth: AuthContract
    usage: AdapterUsagePlan
    formats: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _AdapterRegistration:
    module_paths: tuple[str, ...]
    spec: _AdapterContractSpec
    expected_entries: tuple[_AdapterFilePin, ...] = ()
```

Delete `factory`, `allowed_external_imports`, `factory_code_sha256`, and
`factory_bindings_sha256`.

- [ ] **Step 2: Make closure loading byte-only**

Change the signature to:

```python
def _load_closure(
    module_paths: object,
) -> tuple[_AdapterFilePin, ...]:
```

Preserve `_validate_paths`, `_open_root`, `_read_component`,
`_recursive_python_files`, `_closure_scan_directory`, descriptor identity
checks, file identity de-duplication, two-pass directory-set checks, maximum
file size, and exact pin creation.

Return only:

```python
return tuple(entries)
```

Remove source decoding, AST parsing, import resolution, external-import
allowlists, and all loaded-object inspection. Adapter bytes are provenance
input only; qualification does not interpret them.

- [ ] **Step 3: Add one complete declarative validator**

Implement:

```python
def _validate_contract_spec(value: object) -> _AdapterContractSpec:
    if type(value) is not _AdapterContractSpec:
        raise AdapterContractError("adapter contract declaration is invalid")
    provider_id = _safe_id(value.provider_id, "provider_id")
    product_tier = _safe_id(value.product_tier, "product_tier")
    adapter_id = _safe_id(value.adapter_id, "adapter_id")
    auth = _validate_auth(value.auth)
    usage = _validate_usage(value.usage)
    formats = value.formats
    if (
        type(formats) is not tuple
        or not formats
        or any(type(item) is not str or item not in ALLOWED_FORMATS
               for item in formats)
        or len(set(formats)) != len(formats)
        or tuple(sorted(formats)) != formats
    ):
        raise AdapterContractError("adapter formats are invalid")
    return _AdapterContractSpec(
        provider_id=provider_id,
        product_tier=product_tier,
        adapter_id=adapter_id,
        auth=auth,
        usage=usage,
        formats=formats,
    )
```

Tighten `_safe_id`, `_validate_auth`, and `_validate_usage` to exact trusted
types:

```python
if type(value) is not str or SAFE_IDENTIFIER.fullmatch(value) is None:
    ...

if type(auth) is not AuthContract or type(auth.mode) is not AuthMode:
    ...

if type(usage) is not AdapterUsagePlan:
    ...
```

Require exact `tuple`, `str`, and `int`; reject `bool` explicitly.

- [ ] **Step 4: Implement data-only registration capture**

Replace `_capture_adapter_registration` with:

```python
def _capture_adapter_registration(
    *,
    module_paths: tuple[str, ...],
    spec: _AdapterContractSpec,
) -> _AdapterRegistration:
    """Capture immutable code and declaration pins without executing an adapter."""
    paths = _validate_paths(module_paths)
    declaration = _validate_contract_spec(spec)
    entries = _load_closure(paths)
    return _AdapterRegistration(
        module_paths=paths,
        spec=declaration,
        expected_entries=entries,
    )
```

No import, callback, factory, module traversal, or callable inspection is
permitted in this function.

- [ ] **Step 5: Construct the active contract inside trusted code**

Replace the factory portion of `load_active_adapter_contract` with:

```python
registration = _ADAPTER_REGISTRY.get((provider, tier))
if type(registration) is not _AdapterRegistration:
    raise AdapterContractError("no active adapter is registered")

entries = _load_closure(registration.module_paths)
if (
    not registration.expected_entries
    or entries != registration.expected_entries
):
    raise AdapterContractError(
        "active adapter files differ from code-owned registration pins"
    )

spec = _validate_contract_spec(registration.spec)
if spec.provider_id != provider or spec.product_tier != tier:
    raise AdapterContractError("adapter contract identity mismatch")

digest = _closure_sha256(entries)
auth_projection = {
    "mode": spec.auth.mode.value,
    "credential_env_names": list(spec.auth.credential_env_names),
}
auth_sha = hashlib.sha256(
    AUTH_CONTRACT_DOMAIN + canonical_json_bytes(auth_projection)
).hexdigest()
quota_sha = hashlib.sha256(
    QUOTA_CONTRACT_DOMAIN
    + canonical_json_bytes(_usage_projection(spec.usage))
).hexdigest()

return AdapterContract(
    provider_id=spec.provider_id,
    product_tier=spec.product_tier,
    adapter_id=spec.adapter_id,
    adapter_code_sha256=digest,
    auth=spec.auth,
    usage=spec.usage,
    formats=spec.formats,
    auth_contract_sha256=auth_sha,
    quota_contract_sha256=quota_sha,
)
```

Do not retain a `replace(contract, ...)` call or any user-produced
`AdapterContract`.

- [ ] **Step 6: Delete the obsolete security grammar**

Remove the now-unused:

- `FORBIDDEN_DYNAMIC_NAMES`;
- `FORBIDDEN_EXTERNAL_IMPORT_ROOTS`;
- `_validate_external_imports`;
- `_module_name`;
- `_resolve_relative_import`;
- `_validate_static_imports`;
- `_code_constant`;
- `_code_projection`;
- `_code_sha256`;
- `_walk_code`;
- `_validate_loaded_factory`;
- every immutable/module/class/function binding projection helper;
- `_loaded_bindings_sha256`; and
- imports used only by those helpers (`ast`, `Callable`, `inspect`, `CodeType`,
  `ModuleType`, and `sys`).

Run:

```bash
rg -n \
  "factory|Callable|ModuleType|CodeType|allowed_external_imports|factory_bindings|_validate_static_imports|_loaded_bindings|FORBIDDEN_DYNAMIC_NAMES|FORBIDDEN_EXTERNAL_IMPORT_ROOTS" \
  tennis_v1/adapter_contract.py
```

Expected: no matches and exit code 1.

- [ ] **Step 7: Run focused GREEN verification**

Run:

```bash
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_adapter_contract.AdapterContractTests -v
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_entitlements.ProviderManifestTests -v
"$INCI_TENNIS_PYTHON" -m compileall -q tennis_v1 tests/tennis_v1
```

Expected: all focused tests pass; compilation exits zero; the inert fixture's
`RuntimeError` never appears.

- [ ] **Step 8: Perform the Task 2 functional review checkpoint**

Review only the spec and Task 2 diff for:

- an executable object in either registry dataclass;
- adapter module import or execution;
- contract identity not bound to registry key and request;
- changed digest domain or projection;
- lost descriptor-safe source drift protection; or
- production registry activation.

Address any Critical or Important finding with a new failing regression before
continuing. Record before/after hashes and results; do not stage or commit.

---

### Task 2: Verify the full boundary and obtain independent adversarial approval

**Files:**

- Modify:
  `.superpowers/sdd/2026-07-27-tennis-v1-entitlement-gate/progress.md`
- Modify:
  `.superpowers/sdd/2026-07-27-tennis-v1-entitlement-gate/task-2-report.md`
- Create:
  `.superpowers/sdd/2026-07-27-tennis-v1-entitlement-gate/task-2-declarative-review-package.md`

**Interfaces:**

- Consumes the complete Task 2 implementation and test evidence.
- Produces a reviewable evidence package and either:
  - `Task 2: complete`, allowing the parent plan to consider Task 3; or
  - `Task 2: blocked`, with no downstream work started.

- [ ] **Step 1: Run the complete offline verification matrix**

Run:

```bash
"$INCI_TENNIS_PYTHON" tools/verify_runtime.py
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_adapter_contract.AdapterContractTests -v
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_entitlements.ProviderManifestTests -v
"$INCI_TENNIS_PYTHON" -m unittest discover -s tests/tennis_v1 -v
"$INCI_TENNIS_PYTHON" -m unittest \
  tests.tennis_v1.test_legacy_baseline -v
"$INCI_TENNIS_PYTHON" tests.py
"$INCI_TENNIS_PYTHON" -m compileall -q tennis_v1 tests/tennis_v1
git diff --check
```

Expected:

- every command exits zero;
- all Tennis v1 tests pass;
- the protected legacy guard passes;
- `tests.py` reports `ALL TESTS PASS (200 tests)`;
- no network request occurs; and
- `git diff --check` prints nothing.

- [ ] **Step 2: Prove the execution surface is absent**

Run:

```bash
rg -n \
  "factory|allowed_external_imports|factory_code_sha256|factory_bindings_sha256|_loaded_bindings_sha256|_validate_loaded_factory|_validate_static_imports" \
  tennis_v1/adapter_contract.py
rg -n "_ADAPTER_REGISTRY.*=" tennis_v1/adapter_contract.py
rg -n "synthetic_adapter" tennis_v1 tests/tennis_v1
```

Expected:

- the first scan has no matches;
- the registry assignment is exactly the typed empty dictionary;
- synthetic adapter references are file paths only, never imports; and
- the fixture contains a raising body that the passing suite proves was never
  executed.

- [ ] **Step 3: Update the Task 2 evidence report**

Change the report status from stale `DONE` to the final reviewed state. Include:

- the reproduced transitive re-export bypass that forced the reset;
- the approved declarative decision and spec path;
- the initial RED output;
- the focused and full GREEN commands and exact test counts;
- the source-scan result;
- before/after SHA-256 for every changed implementation/test file;
- confirmation that the production registry is empty;
- confirmation that no provider code, network, order path, staging, commit, or
  protected-v6 edit occurred; and
- any residual risk deferred to the Phase 2 runtime loader.

- [ ] **Step 4: Create the independent review package**

The package must ask a fresh reviewer to reproduce or rule out:

1. any way registration or loading executes adapter-controlled Python;
2. any callable, module, class, mutable container, or subclass-smuggled value
   accepted as a declaration;
3. any way changed source bytes retain the old adapter digest;
4. any identity mismatch accepted between request, registry key, and spec;
5. any authentication or quota digest change;
6. any production provider activation;
7. any protected v6 byte change; and
8. any new Critical or Important regression.

Include the approved spec, this implementation plan, exact file hashes, exact
verification commands, and the prior bypass description. Require the final
line:

```text
All findings addressed: YES|NO
```

- [ ] **Step 5: Obtain two-stage independent review**

First request spec-compliance review. Only after it passes, request code-quality
and adversarial review from a fresh reviewer. Reviewers must not edit files.

For every Critical or Important finding:

1. reproduce it locally;
2. add a failing regression;
3. implement the smallest in-scope correction;
4. rerun the full matrix; and
5. issue a new review package.

If the finding requires a new architecture or exceeds five correction rounds,
mark Task 2 blocked and return to design approval. Do not start parent Task 3.

- [ ] **Step 6: Close the Task 2 gate**

Only when both independent stages conclude with no Critical or Important
finding, update `progress.md` to:

```text
Task 2: complete (declarative adapter registry; executable factory removed;
focused and full verification passed; spec-compliance and adversarial quality
reviews approved; production registry empty)
```

Keep the parent Task 3 pending until its own plan and authorization are
confirmed. Do not stage or commit while `BASE_SNAPSHOT_UNCOMMITTED` remains
active.
