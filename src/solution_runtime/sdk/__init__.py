"""Build-time tooling that turns a solution's declared module dependencies into
a vendored, gateway-bound SDK its handlers import out of the box.

Two pieces:

- `facade_gen` — a protoc plugin that emits a typed facade over the generated
  protobuf message types, routing every call through the runtime's
  `Gateway.unary(procedure, request, response_type)` seam. This is what lets a
  handler write `accounts(gw).audit().query_audit_log(req)` instead of threading
  a raw procedure string. The runtime stays module-agnostic; all module
  knowledge lives in the generated code.
- `sync` — the driver that reads `solution-sdk.yaml`, fetches each declared
  proto at its pinned ref, generates message types + facade, and vendors the
  result into the solution.

Neither is imported by the runtime at serve time; this is a dev dependency.
"""
