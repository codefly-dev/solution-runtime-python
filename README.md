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
