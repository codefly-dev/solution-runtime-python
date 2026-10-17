# Bounded JSON route qualification

The report records the clean implementation, built wheel digest and source hashes.
All 102 tests pass against the installed wheel, including 49 new route cases.
The installed runtime modules were compared byte for byte with the clean source.
Three existing SDK sync cases require buf and were skipped because it is absent;
protoc-dependent generator cases passed with the locally available protoc.

Native loopback HTTP proves query/body bounds, framing rejection before handler
invocation, strict JSON parsing, an absolute body-read timeout, separate credentials
for concurrent requests, status/error behavior and a single invocation when the
caller disconnects before the reply. The fixture credentials are non-authorizing.
No tokens, HTTP captures, databases or service state are retained.

Reproduce by building and installing the wheel with the package's SDK extra and
pytest, then running `python -m pytest tests -q -rs` with protoc/buf on PATH for
the existing generator cases. README documents the API, limits and prerequisites.

This qualifies an opt-in synchronous JSON carrier only. Real caller verification,
resource permissions, application integration, production transport/deployment,
streaming and approved storage remain separate acceptance work. Consumers must
explicitly adopt an immutable source/package pin and qualify their composition.
