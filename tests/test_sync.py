"""Tests for the sync driver.

The pure pieces — manifest parsing, the bindings buf.gen.yaml, and the
SOURCE.txt pin record — run offline. The descriptor-driven facade step is
exercised against a real `buf build` image. The full `sync` (which shells out to
`codefly generate proto`) needs codefly + Docker + network and is not run here.
"""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
import yaml

from solution_runtime.sdk import sync

FIXTURES = Path(__file__).parent / "fixtures"


def _write_manifest(tmp_path: Path, services: str = "[AuditService]") -> Path:
    path = tmp_path / "solution-sdk.yaml"
    path.write_text(
        textwrap.dedent(
            f"""
            version: 1
            out: backend/_sdk
            dependencies:
              - module: accounts
                package: saas.accounts.v1
                source:
                  repo: https://github.com/codefly-dev/module-saas-starter
                  ref: a44ac5aa0b9d019ad8ad659b474c36d81bf9ed50
                  subdir: module/services/accounts/proto
                services: {services}
            """
        )
    )
    return path


def test_load_manifest(tmp_path):
    manifest = sync.load_manifest(_write_manifest(tmp_path))

    assert manifest.out == (tmp_path / "backend/_sdk").resolve()
    (dependency,) = manifest.dependencies
    assert dependency.module == "accounts"
    assert dependency.package == "saas.accounts.v1"
    assert dependency.services == ("AuditService",)
    assert dependency.source.ref == "a44ac5aa0b9d019ad8ad659b474c36d81bf9ed50"
    assert dependency.source.subdir == "module/services/accounts/proto"


def test_bindings_buf_gen_is_messages_only():
    document = yaml.safe_load(sync.render_bindings_buf_gen())

    remotes = [p["remote"] for p in document["plugins"]]
    assert remotes == ["buf.build/protocolbuffers/python", "buf.build/protocolbuffers/pyi"]
    assert all("connect" not in r for r in remotes)


def test_write_source_records_every_pin(tmp_path):
    manifest = sync.load_manifest(_write_manifest(tmp_path))
    tmp_path.joinpath("out").mkdir()
    sync.write_source(tmp_path / "out", manifest.dependencies)

    text = (tmp_path / "out" / "SOURCE.txt").read_text()
    assert "accounts: saas.accounts.v1" in text
    assert "module-saas-starter@a44ac5aa0b9d019ad8ad659b474c36d81bf9ed50" in text


@pytest.mark.skipif(shutil.which("buf") is None, reason="buf not installed")
def test_generate_facade_from_descriptor(tmp_path):
    # buf needs a module root; the fixture tree is a bare set of protos.
    proto_root = tmp_path / "proto"
    shutil.copytree(FIXTURES, proto_root)
    (proto_root / "buf.yaml").write_text(yaml.safe_dump({"version": "v2", "modules": [{"path": "."}]}))
    subprocess.run(["buf", "mod", "update"], cwd=proto_root, check=False)

    out = tmp_path / "_sdk"
    out.mkdir()
    dependency = sync.Dependency(
        module="accounts",
        package="saas.accounts.v1",
        source=sync.Source("repo", "ref", "."),
        services=("AuditService",),
    )
    sync._generate_facade(proto_root, dependency, out)

    facade = (out / "accounts.py").read_text()
    assert "def accounts(gateway):" in facade
    assert '"/saas.accounts.v1.AuditService/QueryAuditLog"' in facade
    assert "IdentityService" not in facade  # declared subset honored
