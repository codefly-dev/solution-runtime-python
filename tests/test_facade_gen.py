"""End-to-end test of the facade generator against a real protoc descriptor.

Compiles the fixture proto with `protoc`, feeds the descriptor to the generator,
then imports the generated facade alongside the generated `*_pb2` modules and
drives a call through a fake gateway — proving the emitted procedure string,
request pass-through, and response type are correct.
"""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from google.protobuf.compiler import plugin_pb2
from google.protobuf.descriptor_pb2 import FileDescriptorSet

from solution_runtime.sdk import facade_gen

FIXTURES = Path(__file__).parent / "fixtures"
PROTO = "saas/accounts/v1/audit.proto"

pytestmark = pytest.mark.skipif(shutil.which("protoc") is None, reason="protoc not installed")


def _descriptor_set(tmp_path: Path) -> FileDescriptorSet:
    out = tmp_path / "descriptors.pb"
    subprocess.run(
        ["protoc", "-I", str(FIXTURES), "--include_imports", f"--descriptor_set_out={out}", PROTO],
        check=True,
    )
    return FileDescriptorSet.FromString(out.read_bytes())


def _request(tmp_path: Path, parameter: str) -> plugin_pb2.CodeGeneratorRequest:
    request = plugin_pb2.CodeGeneratorRequest()
    request.proto_file.extend(_descriptor_set(tmp_path).file)
    request.file_to_generate.append(PROTO)
    request.parameter = parameter
    return request


def _compile_pb2(tmp_path: Path) -> Path:
    root = tmp_path / "sdk"
    root.mkdir()
    subprocess.run(
        ["protoc", "-I", str(FIXTURES), f"--python_out={root}", f"--pyi_out={root}", PROTO],
        check=True,
    )
    for package in ("saas", "saas/accounts", "saas/accounts/v1"):
        (root / package / "__init__.py").touch()
    return root


class FakeGateway:
    def __init__(self, response):
        self._response = response
        self.calls: list[tuple] = []

    def unary(self, procedure, request, response_type):
        self.calls.append((procedure, request, response_type))
        return self._response


def _load_facade(root: Path, name: str):
    sys.path.insert(0, str(root))
    try:
        for mod in list(sys.modules):
            if mod == name or mod.startswith("saas"):
                del sys.modules[mod]
        return importlib.import_module(name)
    finally:
        sys.path.remove(str(root))


def test_generates_facade_for_every_service(tmp_path):
    content = facade_gen.generate(_request(tmp_path, "module=accounts")).file[0].content
    assert "def accounts(gateway):" in content
    assert "class AuditServiceClient:" in content
    assert "class IdentityServiceClient:" in content
    assert "def audit(self):" in content
    assert "def identity(self):" in content
    assert "def query_audit_log(self, request):" in content
    assert '"/saas.accounts.v1.AuditService/QueryAuditLog"' in content


def test_declared_subset_drops_other_services(tmp_path):
    content = facade_gen.generate(
        _request(tmp_path, "module=accounts,services=AuditService")
    ).file[0].content
    assert "class AuditServiceClient:" in content
    assert "IdentityService" not in content


def test_facade_routes_through_gateway(tmp_path):
    root = _compile_pb2(tmp_path)
    facade = facade_gen.generate(_request(tmp_path, "module=accounts")).file[0]
    (root / facade.name).write_text(facade.content)

    accounts = _load_facade(root, "accounts")
    audit_pb2 = importlib.import_module("saas.accounts.v1.audit_pb2")

    expected = audit_pb2.QueryAuditLogResponse(next_page_token="cursor")
    gateway = FakeGateway(expected)
    request = audit_pb2.QueryAuditLogRequest(page_size=20)

    result = accounts.accounts(gateway).audit().query_audit_log(request)

    assert result is expected
    (procedure, sent, response_type) = gateway.calls[0]
    assert procedure == "/saas.accounts.v1.AuditService/QueryAuditLog"
    assert sent is request
    assert response_type is audit_pb2.QueryAuditLogResponse
