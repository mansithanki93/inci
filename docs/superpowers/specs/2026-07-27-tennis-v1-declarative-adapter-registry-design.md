# Tennis v1 Declarative Adapter Registry Design

**Status:** Approved in conversation on 2026-07-27; written specification
awaiting operator review.

## 1. Purpose

Replace the executable adapter-contract factory with an immutable, code-owned
declaration. Provider adapter Python must not execute, import modules, or
contribute live Python objects while Tennis v1 decides whether a provider tier
is entitled and qualified.

This redesign closes the Task 2 boundary failure demonstrated during
adversarial review: an allowed external module could re-export another module,
recover Python's import machinery, and load unpinned local code without
changing the pinned adapter files.

This change affects entitlement qualification only. It does not enable a
provider, provider networking, demo orders, or live orders.

## 2. Decision

Use a declarative registry rather than attempting to expand the executable
factory blacklist.

The rejected alternatives are:

1. **A larger Python import/reflection blacklist.** This remains incomplete by
   construction because allowed modules can expose unexpected transitive
   objects.
2. **A restricted Python factory interpreter.** This creates a new language
   and security boundary without providing value needed by Phase 1.
3. **A subprocess or operating-system sandbox.** This is unnecessarily complex
   for static provider, authentication, quota, and format metadata and would
   still require a separately reviewed runtime-loader boundary.

The registry declaration is sufficient because the contract values are static
facts. They do not need provider-controlled computation.

## 3. Architecture

### 3.1 Immutable declaration

Add a private frozen, slotted `_AdapterContractSpec` containing only:

- `provider_id: str`
- `product_tier: str`
- `adapter_id: str`
- `auth: AuthContract`
- `usage: AdapterUsagePlan`
- `formats: tuple[str, ...]`

Change `_AdapterRegistration` to contain only:

- `module_paths: tuple[str, ...]`
- `spec: _AdapterContractSpec`
- `expected_entries: tuple[_AdapterFilePin, ...]`

It must not contain a callable, function, class, module, code object, import
allowlist, or loaded object-graph digest.

The existing `_ADAPTER_REGISTRY` remains a private, code-owned mapping keyed by
exact `(provider_id, product_tier)`. It remains empty in production during
Phase 1.

### 3.2 Registration capture

`_capture_adapter_registration` accepts only `module_paths` and a declarative
spec. It:

1. validates the exact closed Python-file set;
2. safely opens and reads each regular, single-link file;
3. captures each file's path, length, and SHA-256;
4. validates the complete spec; and
5. returns an immutable registration.

Registration capture parses no adapter imports and executes no adapter code.
Supplying callables, modules, mutable containers, or values of the wrong exact
type fails closed.

### 3.3 Contract loading

`load_active_adapter_contract(provider_id, product_tier)` performs this exact
flow:

1. strictly validate both requested identifiers;
2. resolve the exact registry entry or deny;
3. re-read the closed file set through the existing descriptor-safe path;
4. require the observed file pins to equal the captured pins;
5. compute `adapter_code_sha256` from the canonical file-pin projection;
6. revalidate the immutable declaration;
7. require declaration identity to equal the registry key and request;
8. compute the existing authentication and quota digests; and
9. construct and return `AdapterContract` inside trusted Tennis v1 code.

No adapter import, function call, class construction, module traversal, or
dynamic lookup occurs in this flow.

### 3.4 Digest compatibility

The adapter-code digest remains:

```text
SHA-256(
  b"INCI-ADAPTER-CLOSURE-V1\0"
  + canonical_json([
      {"path": path, "length": length, "sha256": file_sha256},
      ...
    ])
)
```

The authentication and quota domains and projections remain unchanged. This
preserves qualification-artifact binding and prevents a migration from
silently changing existing provenance semantics.

## 4. Security and Failure Behavior

All failures remain fail-closed:

- unknown provider or tier;
- malformed or mutable registry objects;
- empty, duplicate, unsorted, absolute, traversing, missing, symlinked,
  hard-linked, oversized, or changed adapter files;
- unexpected Python files inside the closed adapter directory;
- declaration identity that differs from the registry key or request;
- invalid authentication mode or credential environment names;
- invalid quota integers; or
- unsupported, empty, duplicate, or unsorted formats.

Validation uses exact expected dataclass types where subclass behavior could
introduce executable or mutable semantics. Error messages identify the failed
field but do not expose credentials; declarations contain credential variable
names only.

Because no untrusted Python executes, qualification no longer depends on
trying to enumerate Python reflection or import escape paths.

## 5. Code Changes

### `tennis_v1/adapter_contract.py`

- Add `_AdapterContractSpec`.
- Replace factory-based `_AdapterRegistration`.
- Simplify `_capture_adapter_registration`.
- Make `load_active_adapter_contract` construct the result directly.
- Remove factory code hashing, object-graph projection, AST import allowlists,
  reflection blacklists, and their imports when no longer used.
- Preserve closed-file reading, file-pin hashing, contract validation, quota
  derivation, and digest formulas.

### `tests/tennis_v1/fixtures/synthetic_adapter.py`

- Retain only inert file bytes needed to exercise closure pinning, or replace
  it with an inert fixture module.
- Remove contract factory functions.

### `tests/tennis_v1/test_adapter_contract.py`

- Replace executable-factory tests with declarative-boundary tests.
- Retain closed-file and digest regressions.
- Add explicit proof that capture and load execute no adapter code or imports.
- Add rejection tests for callable/module/mutable registration content.
- Add deterministic repeated-load and identity-mismatch tests.
- Add regression coverage for the demonstrated transitive re-export class by
  proving no adapter namespace is loaded or traversed at all.

### `tests/tennis_v1/test_entitlements.py`

- Build the synthetic registration from `_AdapterContractSpec`.
- Preserve provider-manifest, qualification, trace-format, adapter-digest,
  quota, and entitlement behavior tests.

### Task ledger and review report

- Record the architectural reset, focused test evidence, full-suite evidence,
  and independent adversarial review result.

No protected v6 file is changed by this redesign.

## 6. Test Strategy

Implementation follows red-green-refactor:

1. First replace or add tests that fail against the callable factory model.
2. Make the smallest production change that satisfies the declarative
   contract.
3. Remove obsolete security machinery only after behavior is covered.

Required verification:

- focused adapter-contract tests;
- provider entitlement tests;
- all Tennis v1 tests;
- protected legacy SHA-256 guard;
- the existing 200-test v6 suite;
- `git diff --check`;
- a source scan proving the registration path contains no factory, callable,
  module, import-allowlist, or loaded-binding mechanism; and
- an independent adversarial review using a fresh review package.

Task 2 is complete only when all required checks pass and independent review
finds no Important or Critical issue. Task 3 remains blocked until then.

## 7. Deferred Runtime Provider Loader

Executing a real provider adapter belongs to a later, separately designed
Phase 2 loader. That loader must consume the exact qualified provider binding
and pinned adapter digest but cannot inherit authority merely because the
declarative entitlement contract passed.

This design grants no authority to build that loader, contact a provider, use
a trial, retain provider data, or place orders.

## 8. Operational Consequences

- The current executable synthetic-factory tests are replaced, not carried
  forward as a second hidden execution path.
- Production remains unable to activate any provider because the production
  registry is empty.
- Adapter contract metadata changes require a reviewed code change.
- Adapter source changes invalidate captured pins and all qualification
  artifacts bound to the previous adapter digest.
- The redesign reduces Task 2 code and attack surface, but deliberately
  postpones provider runtime execution to its own security review.
