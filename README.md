# solution-runtime (python)

Generic Python runtime for **codefly solutions**. Twin of solution-runtime-go:
registration (host + gateway, heartbeat), CORS, Module Federation asset serving,
the capability handshake, the manifest, and a bearer-forwarding gateway client.

```python
from solution_runtime import Gateway, Solution

def handler(gw: Gateway) -> dict:
    resp = gw.unary("/pkg.Service/Method", Request(), Response)
    return {...}

Solution(id="my-solution", title="My Solution").handle("/thing", handler).serve()
```

The gateway URL, the caller's bearer, and the wire protocol are hidden.

## Per-solution SDK (`codefly sync solution-sdk`)

Threading a raw procedure string — `gw.unary("/saas.accounts.v1.AuditService/QueryAuditLog", req, Resp)` — and vendoring a whole module's generated `gen/` tree are the two things every solution used to repeat. Both are now generated for you: a solution declares the APIs it calls in `solution.codefly.yaml`, and `codefly sync solution-sdk` builds **one SDK for that solution, carrying only the functionality it declares**, resolving each contract from the composed module package (no hand-pinned git ref):

```yaml
# solution.codefly.yaml
api:
  consumes:
    - module: accounts # the entry-point name: accounts(gw)
      package: saas.accounts.v1 # the proto package to bind
      services: [AuditService] # optional: the subset actually used
```

```bash
codefly sync solution-sdk --language python
```

Handlers use the generated SDK out of the box:

```python
import sys, pathlib

# The vendored root goes on sys.path, like any generated-proto tree — the
# facade and the *_pb2 modules it imports both resolve from there.
sys.path.insert(0, str(pathlib.Path(__file__).with_name("_sdk")))

from accounts import accounts

def last_login(gw: Gateway) -> dict:
    resp = accounts(gw).audit().query_audit_log(QueryAuditLogRequest(page_size=20))
    ...
```

The generated facade is one class per service, one method per RPC, each routing through this runtime's `Gateway.unary` seam. The procedure string and response type are baked in by the generator, so a handler never threads either — and only the declared `services` are present. **`Gateway.unary(procedure, request, response_type)` is the seam the facade binds to**; the runtime never learns what "accounts" is, so a second module's SDK binds the same way.

The facade generator itself now lives in codefly's proto companion (Python, Go and TypeScript), and the CLI resolves contracts from the module package rather than fetching a producer's git repo. This runtime keeps only the seam.

### Migrating from `solution-sdk`

The old `solution-sdk` CLI and its `solution-sdk.yaml` (`source.repo/ref/subdir`) are deprecated. `solution-sdk sync` is now a shim: with a `solution.codefly.yaml` present, it runs `codefly sync solution-sdk --language python`; otherwise it runs the legacy fetch-and-vendor path once, under a deprecation warning. Move each `dependencies:` entry into `api.consumes` (the `source:` block is dropped — contracts come from the composed module package) and delete `solution-sdk.yaml`. The legacy path and the `solution-runtime[sdk]` extra are removed in the next minor.
