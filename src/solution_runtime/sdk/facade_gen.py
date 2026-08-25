"""protoc plugin `protoc-gen-solution_facade`.

Reads a `CodeGeneratorRequest` and, for every service in the files being
generated, emits a typed Python facade bound to a solution-runtime `Gateway`.
Each RPC becomes a method that calls `gateway.unary(procedure, request,
response_type)` — the procedure string and the response type are baked in by the
generator, so a handler never threads either by hand:

    from accounts import accounts

    resp = accounts(gw).audit().query_audit_log(
        QueryAuditLogRequest(page_size=20),
    )

The facade only needs the generated `*_pb2` message classes and the gateway's
`unary` seam; it deliberately does not depend on Connect client stubs — the
runtime already owns the transport.
"""

from __future__ import annotations

import re
import sys

from google.protobuf.compiler import plugin_pb2
from google.protobuf.descriptor_pb2 import FileDescriptorProto, ServiceDescriptorProto

_CAMEL_BOUNDARY_1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_BOUNDARY_2 = re.compile(r"([a-z0-9])([A-Z])")
_VERSION_SEGMENT = re.compile(r"v\d+")


def _snake(name: str) -> str:
    stepped = _CAMEL_BOUNDARY_1.sub(r"\1_\2", name)
    return _CAMEL_BOUNDARY_2.sub(r"\1_\2", stepped).lower()


def _pb2_module(file_name: str) -> str:
    """`saas/accounts/v1/audit.proto` -> `saas.accounts.v1.audit_pb2`."""
    return file_name.removesuffix(".proto").replace("/", ".") + "_pb2"


def _default_module_name(package: str) -> str:
    """`saas.accounts.v1` -> `accounts` — the entry-point name when the driver
    does not pass an explicit `module=` option."""
    segments = [s for s in package.split(".") if not _VERSION_SEGMENT.fullmatch(s)]
    return segments[-1] if segments else package.replace(".", "_")


def _parse_parameter(parameter: str) -> dict[str, str]:
    options: dict[str, str] = {}
    for item in filter(None, (parameter or "").split(",")):
        key, _, value = item.partition("=")
        options[key.strip()] = value.strip()
    return options


class _TypeIndex:
    """Maps a fully-qualified message name to the `*_pb2` module that defines it
    and the attribute path within that module."""

    def __init__(self, files: list[FileDescriptorProto]):
        self._module_of: dict[str, str] = {}
        self._attr_of: dict[str, str] = {}
        for fd in files:
            module = _pb2_module(fd.name)
            prefix = f".{fd.package}" if fd.package else ""
            self._walk(prefix, "", fd.message_type, module)

    def _walk(self, name_prefix, attr_prefix, messages, module):
        for message in messages:
            full = f"{name_prefix}.{message.name}"
            attr = f"{attr_prefix}{message.name}"
            self._module_of[full] = module
            self._attr_of[full] = attr
            self._walk(full, f"{attr}.", message.nested_type, module)

    def module_of(self, full_name: str) -> str:
        return self._module_of[full_name]

    def reference(self, full_name: str, alias: str) -> str:
        return f"{alias}.{self._attr_of[full_name]}"


def _accessor_name(service_name: str) -> str:
    return _snake(service_name.removesuffix("Service"))


def _class_name(service_name: str) -> str:
    return service_name if service_name.endswith("Client") else f"{service_name}Client"


def render(
    files: list[FileDescriptorProto],
    types: _TypeIndex,
    module: str,
    selected: frozenset[str],
) -> str | None:
    services: list[tuple[str, ServiceDescriptorProto]] = []
    for fd in files:
        for service in fd.service:
            if selected and service.name not in selected:
                continue
            services.append((fd.package, service))
    if not services:
        return None

    aliases: dict[str, str] = {}

    def alias_for(module_name: str) -> str:
        if module_name not in aliases:
            aliases[module_name] = f"_pb{len(aliases)}"
        return aliases[module_name]

    service_blocks: list[str] = []
    class_names: dict[str, tuple[str, str]] = {}
    for package, service in services:
        class_name = _class_name(service.name)
        if class_name in class_names:
            other_pkg, other_svc = class_names[class_name]
            raise ValueError(
                f"service class collision: {package}.{service.name} and "
                f"{other_pkg}.{other_svc} both map to {class_name}"
            )
        class_names[class_name] = (package, service.name)
        lines = [
            f"class {class_name}:",
            "    def __init__(self, gateway):",
            "        self._gateway = gateway",
            "",
        ]
        method_names: dict[str, str] = {}
        for method in service.method:
            method_name = _snake(method.name)
            if method_name in method_names:
                raise ValueError(
                    f"method name collision in {package}.{service.name}: "
                    f"{method.name} and {method_names[method_name]} both map to {method_name}"
                )
            method_names[method_name] = method.name
            response_alias = alias_for(types.module_of(method.output_type))
            response_ref = types.reference(method.output_type, response_alias)
            procedure = f"/{package}.{service.name}/{method.name}"
            lines += [
                f"    def {method_name}(self, request):",
                f'        return self._gateway.unary("{procedure}", request, {response_ref})',
                "",
            ]
        service_blocks.append("\n".join(lines).rstrip())

    client_class = f"_{module[:1].upper()}{module[1:]}Client"
    client_lines = [
        f"class {client_class}:",
        "    def __init__(self, gateway):",
        "        self._gateway = gateway",
        "",
    ]
    accessor_names: dict[str, str] = {}
    for package, service in services:
        accessor = _accessor_name(service.name)
        if accessor in accessor_names:
            raise ValueError(
                f"service accessor collision: {service.name} and "
                f"{accessor_names[accessor]} both map to {accessor}()"
            )
        accessor_names[accessor] = service.name
        client_lines += [
            f"    def {accessor}(self):",
            f"        return {_class_name(service.name)}(self._gateway)",
            "",
        ]

    import_lines = [f"import {name} as {alias}" for name, alias in aliases.items()]

    header = [
        f'"""Gateway-bound facade for the `{module}` module — generated, do not edit.',
        "",
        "Generated by protoc-gen-solution_facade from the module's public proto.",
        "Every method routes through a solution-runtime Gateway whose",
        "`unary(procedure, request, response_type)` owns the transport, bearer, and",
        "wire protocol.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
    ]

    factory = [
        f"def {module}(gateway):",
        f'    """Entry point: `{module}(gw).<service>().<rpc>(request)`."""',
        f"    return {client_class}(gateway)",
    ]

    preamble = "\n".join(header + import_lines).rstrip()
    chunks = service_blocks + ["\n".join(client_lines).rstrip(), "\n".join(factory)]
    return preamble + "\n\n\n" + "\n\n\n".join(chunks) + "\n"


def generate(request: plugin_pb2.CodeGeneratorRequest) -> plugin_pb2.CodeGeneratorResponse:
    response = plugin_pb2.CodeGeneratorResponse()
    response.supported_features = plugin_pb2.CodeGeneratorResponse.FEATURE_PROTO3_OPTIONAL

    types = _TypeIndex(list(request.proto_file))
    options = _parse_parameter(request.parameter)
    targets = [fd for fd in request.proto_file if fd.name in set(request.file_to_generate)]
    selected = frozenset(filter(None, options.get("services", "").split("+")))

    if selected:
        available = {service.name for fd in targets for service in fd.service}
        unknown = selected - available
        if unknown:
            raise ValueError(f"declared services not found in the proto: {sorted(unknown)}")

    # A service's fully-qualified name — hence its facade — is per package, so
    # each package gets its own file rather than being merged under one factory.
    groups: dict[str, list[FileDescriptorProto]] = {}
    for fd in targets:
        if fd.service:
            groups.setdefault(fd.package, []).append(fd)

    explicit_module = options.get("module")
    emitted: dict[str, str] = {}
    for package, files in groups.items():
        module = explicit_module if explicit_module and len(groups) == 1 else _default_module_name(package)
        content = render(files, types, module, selected)
        if content is None:
            continue
        name = f"{module}.py"
        if name in emitted:
            raise ValueError(
                f"module name collision: packages {package} and {emitted[name]} both map to {module}"
            )
        emitted[name] = package
        generated = response.file.add()
        generated.name = name
        generated.content = content
    return response


def main() -> None:
    request = plugin_pb2.CodeGeneratorRequest.FromString(sys.stdin.buffer.read())
    try:
        response = generate(request)
    except ValueError as error:
        response = plugin_pb2.CodeGeneratorResponse(error=str(error))
    sys.stdout.buffer.write(response.SerializeToString())


if __name__ == "__main__":
    main()
