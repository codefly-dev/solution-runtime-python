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

The facade generator is moving into codefly's proto companion (Python, Go and TypeScript; codefly-dev/core#384), where the CLI resolves contracts from the module package rather than fetching a producer's git repo. Until that ships, this runtime still carries the generator — it also runs as a standalone protoc/buf plugin, `protoc-gen-solution_facade` — but the seam it binds to is all the runtime owns long-term.

### Migrating from `solution-sdk`

The old `solution-sdk` CLI and its `solution-sdk.yaml` (`source.repo/ref/subdir`) are deprecated. `solution-sdk sync` is now a shim: with a `solution.codefly.yaml` present, it runs `codefly sync solution-sdk --language python`; otherwise it runs the legacy fetch-and-vendor path once, under a deprecation warning. Move each `dependencies:` entry into `api.consumes` (the `source:` block is dropped — contracts come from the composed module package) and delete `solution-sdk.yaml`. The legacy path and the `solution-runtime[sdk]` extra are removed in the next minor.

## Bounded application JSON routes

Use `route` when an application needs query selectors, request bodies or explicit
HTTP statuses. Existing `handle(path, gateway_handler)` GET routes remain
compatible. Routes match exact paths; GET and POST can share a path.

```python
from solution_runtime import Request, RequestError, Response, Solution


def create_item(gateway, request: Request) -> Response:
    data = request.json()
    if not isinstance(data, dict) or set(data) != {"name"}:
        raise RequestError(400, "Expected a name")
    # Resolve the caller and authorize the selected resource through the existing
    # platform SDK before storage or effects. Keep request identity/idempotency
    # in the application; a lost HTTP reply is not proof that nothing happened.
    return Response({"accepted": data["name"]}, status=202)


Solution(id="example", title="Example").route(
    "/items", create_item, method="POST"
).serve()
```

Each invocation receives a fresh Gateway bound to the single incoming Bearer
credential and an immutable Request with `method`, `path`, read-only `query` and
raw `body` bytes. The runtime checks credential presence/shape only: the platform
must authenticate and authorize it. Cookies and caller-supplied identity headers
are not used to create a principal. No identity verification, token refresh,
mutation retry, persistence, history or domain execution is added to this runtime.

The opt-in routes enforce these transport limits before invoking application code:

- Request target: 4 KiB; at most 32 query fields. Duplicate fields, malformed
  escapes, invalid UTF-8 and control characters are rejected.
- POST: one Content-Length, at most 64 KiB and UTF-8 `application/json` content
  type. Chunked/ambiguous framing is rejected. GET bodies are rejected.
- Body read: five seconds total, including slow incremental delivery. Incomplete
  or timed-out bodies never reach the handler.
- `request.json()` rejects duplicate object keys, non-finite values, invalid UTF-8
  and invalid JSON. Call it and validate the schema before any mutation.
- Response: explicit `Response(body, status=200)`, serialized JSON at most 1 MiB;
  no redirects or bodyless statuses. The handler must bound its own object
  construction. Responses set no-store, nosniff and a fixed content length.

`RequestError(status, public_message)` produces an intentional 4xx/5xx error.
Unexpected handler/output errors return a generic 500 without exception text.
Application 401/403/404/409/429/503 statuses are preserved. A request invokes its
handler once; a client disconnect does not retry or roll back application work.
The application owns durable idempotency and recovery under the original key.
The connection closes after each JSON route response, including framing errors.

This is a synchronous JSON interface. It does not add SSE, async handler
cancellation, a handler execution deadline, inbound header/connection quotas,
upstream Gateway transport hardening, cookie/CSRF authentication or a deployment.
Existing CORS, registration, manifests and static asset behavior are retained.
Trusted ingress, account-switch fencing, resource authorization, streaming and
production limits require qualification in the owning host/application composition.

Tests use real loopback HTTP for caller separation, framing/JSON limits, slow
body timeout, output/status handling and lost-acknowledgement behavior:

```sh
python -m pip install -e '.[sdk]' pytest
python -m pytest tests -q
```

SDK generator tests additionally require their existing protoc/buf tools; the
request-route tests have no generator or external-service dependency.

### Bounded event streams

An exact GET `route` may return `EventStream(iterator)` of typed `Event` values.
The same incoming bearer and bounded `Request.last_event_id` reach the application.
The application must authenticate before returning data, recheck authority before
subsequent events, bound each iterator step with an upstream timeout, and implement
`close()` to release upstream resources. A generator's `finally` is sufficient.
The runtime prefetches the first event before success headers, flushes each frame,
limits concurrent streams to32, lifetime to60seconds, event count to256 and each
frame to512KiB. Socket writes time out after5seconds. Failures after headers and
client disconnects close the iterator; there is no implicit retry or cancellation.

For current SaaS hosts, configure `SOLUTION_REGISTRATION_SECRET_FILE` and
`SOLUTION_REGISTRATION_INTERNAL_TOKEN_FILE` as protected rotating projections.
The runtime exchanges them at the configured gateway's
`/solutions/_registration-token` before **each** host/backend registration or lease
renewal. Each half uses a distinct single-use token. The solution secret is bound
by the existing Accounts registry to that solution and publisher; configuration
never creates or grants this identity. The resulting token alone reaches the
registration endpoint; long-lived credentials remain at the exchange endpoint.
`SOLUTION_REGISTRATION_CA_FILE` optionally selects the HTTPS trust bundle. Redirects
and environment proxies are disabled. Private mesh HTTP endpoints retain the
normal host transport contract. An exchange failure does not fall back to shared
internal-token registration. Without the opt-in secret file, historical consumers
retain their previous behavior; current hosted LastLogin must enable the signed
registration path. No conversation data or user bearer is used for registration.
