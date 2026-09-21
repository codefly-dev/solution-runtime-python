# AGENTS.md

Generic Python runtime for **codefly solutions** — the twin of
`codefly-dev/solution-runtime-go`.

## What this repo owns

`src/solution_runtime/__init__.py` — everything every solution needs
identically, and nothing any single solution needs:

- self-registration with the host and the gateway, and the heartbeat that
  keeps both alive
- the solution manifest (`/.well-known/solution.json`) and the capability
  handshake (`/.well-known/capabilities`)
- CORS, `/health`, and static Module Federation asset serving under `/assets/`
- `Gateway.unary(procedure, request, response_type)` — the seam that hides the
  gateway URL, the caller's bearer, and the wire protocol

`src/solution_runtime/sdk/` — build-time only, never imported at serve time:
`facade_gen` (the `protoc-gen-solution_facade` plugin) emits a typed facade
bound to that seam, and `sync` drives the legacy vendoring path and shims
`solution-sdk sync` onto `codefly sync solution-sdk`.

## What this repo does not own

- **Any specific solution, host, or module.** The runtime never learns what
  "accounts" is; module knowledge lives in generated code.
- **Proto codegen.** Message bindings come from `codefly generate proto`, never
  a bespoke `buf` invocation.
- **Contract resolution.** The CLI resolves contracts from the composed module
  package. The facade generator is migrating to codefly's proto companion
  (codefly-dev/core#384); long-term this repo owns the *seam*, not the
  generator.
- **Transport/Connect client stubs.** Deliberately omitted — the runtime owns
  the transport.

## Build and test

**This repo has no CI.** Nothing under `.github/` exists, and none ever has, so
there is no pipeline to copy these from. They were verified by running them on
this branch — if you change them, run them again and correct this section.

```bash
uv venv .venv --python 3.11            # pyproject requires >=3.11
uv pip install --python .venv/bin/python -e '.[sdk]' pytest
.venv/bin/python -m pytest -q
```

- The editable install is **required**: the package is a `src/` layout and
  nothing in `pyproject.toml` sets a pytest path, so a bare `pytest` from the
  root cannot import `solution_runtime`.
- `pytest` is declared nowhere in `pyproject.toml` — install it explicitly, as
  above. The `[sdk]` extra (`pyyaml`) is what the `sdk/` tests need.
- Observed: **56 passed** with `protoc` and `buf` on `PATH`. Without them, 45
  pass and 11 skip — 8 in `test_facade_gen.py` (needs `protoc`), 3 in
  `test_sync.py` (needs `buf`). **A green run that skipped 11 is not a green
  run**; install both before claiming the generator works.
- No test reaches the network or Docker. `test_sync.py` stubs `codefly`; full
  `sync` against a real BSR fetch is not covered here.

## Behaviour

These are fleet-wide rules (obin-ai/handbook#68), not local taste.

**A gap in the tooling is a bug in the tooling** — never a reason to reach
around it. Not as a "workaround", not "just this once", not "until the verb
lands".

**Never hack. Always provide the best fix, even when it spans repos.** The
right fix living in someone else's repo is not a reason to work around it in
yours — open the PR there. If it genuinely cannot be fixed now, the deliverable
is a precise issue against the owner *plus* an explicitly-labelled stopgap,
never an unlabelled one.

**Classify every change that makes something work**, in the PR body: a *fix* at
the place that owns the behaviour, or a *hack*. A hack does not become a fix by
working, by being small, by being local, or by the real fix belonging elsewhere.

**Never hardcode what the system resolves.** Injected environment, derived
ports, service addresses, credentials copied out of another component's config,
values read out of a running process. If you are typing it, you are encoding
something true only on your machine for the next ten minutes.

**Diagnose, do not pattern-match.** "It started working when I set X" is not a
diagnosis — set X back and confirm it breaks. Do not trust an error message
before checking it.

**Say what you did not do.** Unverified is not the same as working. If you
could not exercise something, the PR says so.

## Traps specific to this runtime

**Registration fails silently by design, and that is the failure mode this
repo has already shipped once.** `_register_headers()` omits
`x-codefly-internal-token` when `CODEFLY_INTERNAL_TOKEN` is empty or unset, and
`_post_registration()` never raises — the heartbeat loop must survive. A
solution with a missing credential therefore boots, serves, and is simply
absent from the host. `_heartbeat()` prints only on *transitions*, so a
never-healthy registration logs nothing at all. When touching registration,
prove the failure path logs something a human would see.

**The runtime module is standard library only.** `src/solution_runtime/__init__.py`
imports nothing outside stdlib. `protobuf` and `protovalidate` are there for the
message types callers pass through `Gateway.unary`, not for the runtime's own
use. New runtime dependencies need a reason; `sdk/` is where third-party
build-time tools belong.

**Parity with the Go twin is load-bearing.** The two runtimes previously
disagreed on whether a credential is a path or a value, which is how the silent
registration failure above reached production. Changes to registration payloads,
manifest shape, or the capability response should be checked against
`solution-runtime-go` and the divergence named in the PR if you leave one.

**`Gateway.unary` is a published seam.** Generated facades bind to its exact
signature. Changing it breaks every vendored SDK already on disk in solutions
this repo does not own.

## Pull requests

- Conventional Commits for both the commit and the PR title, referencing the
  issue: `fix: keep the registration heartbeat alive (#6)`.
- Body opens with `Closes #N.` on its own line, then `## Summary` (why, not a
  diff recap), then `## Test plan`.
- State which of the 56 tests you actually ran, and whether `protoc`/`buf` were
  present — see the skip note above.
- Update this file in the PR that changes the process it describes. An agent
  reads it on every request; a stale line here is worse than a missing one.
