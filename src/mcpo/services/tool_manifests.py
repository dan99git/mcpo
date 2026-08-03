from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import yaml
from yaml.composer import ComposerError
from yaml.nodes import MappingNode
from yaml.tokens import AliasToken, AnchorToken, ScalarToken, TagToken

from mcpo.services.cli_packages import list_cli_packages
from mcpo.services.skill_packages import (
    MAX_ARCHIVE_ENTRIES,
    MAX_EXPANDED_BYTES,
    SkillPackageError,
    _decode_archive,
    _package_id_from_filename,
    _safe_member_path,
)


MAX_TOOL_MANIFEST_BYTES = 65_536
MAX_TOOL_MANIFESTS = 100
MAX_YAML_NODES = 256
MAX_YAML_DEPTH = 8
MAX_INPUT_PROPERTIES = 32
MAX_ARGUMENTS = 32
MAX_ARGUMENT_BYTES = 1_024
MAX_ARGUMENT_TOTAL_BYTES = 16_384
MAX_OUTPUT_BYTES = 1_048_576
MAX_TIMEOUT_SECONDS = 600

_TOOL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_PACKAGE_ID_RE = re.compile(
    r"^(?:python|npm)-[a-z0-9](?:[a-z0-9-]{0,47})-[0-9a-f]{12}$"
)
_EXECUTABLE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
_INPUT_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PLACEHOLDER_RE = re.compile(r"^\{input\.([a-z][a-z0-9_]{0,63})\}$")


class ToolManifestArchiveError(ValueError):
    pass


class _DuplicateKeyError(yaml.YAMLError):
    pass


class _NonStringKeyError(yaml.YAMLError):
    pass


class _StrictSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(
    loader: _StrictSafeLoader,
    node: MappingNode,
    deep: bool = False,
) -> dict[str, Any]:
    if not isinstance(node, MappingNode):
        raise _NonStringKeyError("YAML mapping node is invalid")
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if type(key) is not str:
            raise _NonStringKeyError("YAML mapping keys must be strings")
        if key in mapping:
            raise _DuplicateKeyError(f"Duplicate YAML key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _issue(path: str, code: str, message: str) -> dict[str, str]:
    return {"path": path, "code": code, "message": message}


def _preview_shell(path: str, raw_manifest: bytes) -> dict[str, Any]:
    return {
        "path": path,
        "manifestSha256": hashlib.sha256(raw_manifest).hexdigest(),
        "normalizedSha256": None,
        "valid": False,
        "resolved": False,
        "errors": [],
        "warnings": [],
        "normalized": None,
        "dependency": {
            "status": "not_checked",
            "message": "Dependency resolution was not checked.",
        },
        "commandPreview": [],
        "executionEligible": False,
        "blockers": [
            "manifest_invalid",
            "approval_registry_unavailable",
            "execution_worker_unavailable",
        ],
    }


def _measure_yaml_tree(value: Any, depth: int = 0) -> tuple[int, int]:
    count = 1
    maximum_depth = depth
    if type(value) is dict:
        for item in value.values():
            child_count, child_depth = _measure_yaml_tree(item, depth + 1)
            count += 1 + child_count
            maximum_depth = max(maximum_depth, child_depth)
    elif type(value) is list:
        for item in value:
            child_count, child_depth = _measure_yaml_tree(item, depth + 1)
            count += child_count
            maximum_depth = max(maximum_depth, child_depth)
    return count, maximum_depth


def _validate_object(
    value: Any,
    path: str,
    required: set[str],
    optional: set[str],
    errors: list[dict[str, str]],
) -> dict[str, Any] | None:
    if type(value) is not dict:
        errors.append(_issue(path, "invalid_type", "Expected an object."))
        return None
    for field in sorted(set(value).difference(required | optional)):
        errors.append(
            _issue(f"{path}.{field}", "unknown_field", f"Unknown field: {field}")
        )
    for field in sorted(required.difference(value)):
        errors.append(
            _issue(
                f"{path}.{field}",
                "missing_field",
                f"Required field is missing: {field}",
            )
        )
    return value


def _validate_description(
    value: Any,
    path: str,
    maximum: int,
    errors: list[dict[str, str]],
) -> str | None:
    if type(value) is not str:
        errors.append(_issue(path, "invalid_type", "Expected a string."))
        return None
    normalized = value.strip()
    if not normalized:
        errors.append(_issue(path, "empty_string", "Value must not be empty."))
        return None
    if len(normalized) > maximum:
        errors.append(
            _issue(path, "string_too_long", f"Value exceeds {maximum} characters.")
        )
        return None
    if any(
        ord(character) < 32 and character not in "\n\r\t"
        for character in normalized
    ):
        errors.append(
            _issue(
                path,
                "invalid_control_character",
                "Value contains control characters.",
            )
        )
        return None
    return normalized


def _validate_integer(
    value: Any,
    path: str,
    minimum: int,
    maximum: int,
    errors: list[dict[str, str]],
) -> int | None:
    if type(value) is not int:
        errors.append(_issue(path, "invalid_integer", "Expected an integer."))
        return None
    if not minimum <= value <= maximum:
        errors.append(
            _issue(
                path,
                "integer_out_of_range",
                f"Value must be between {minimum} and {maximum}.",
            )
        )
        return None
    return value


def _validate_enum_list(
    value: Any,
    path: str,
    allowed: tuple[str, ...],
    errors: list[dict[str, str]],
) -> list[str] | None:
    if type(value) is not list:
        errors.append(_issue(path, "invalid_type", "Expected an array."))
        return None
    seen: set[str] = set()
    accepted: set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if type(item) is not str:
            errors.append(_issue(item_path, "invalid_type", "Expected a string."))
        elif item in seen:
            errors.append(
                _issue(item_path, "duplicate_item", f"Duplicate value: {item}")
            )
        elif item not in allowed:
            errors.append(
                _issue(
                    item_path,
                    "unsupported_capability",
                    f"Unsupported capability: {item}",
                )
            )
        else:
            accepted.add(item)
        if type(item) is str:
            seen.add(item)
    return [item for item in allowed if item in accepted]


def _validate_manifest_schema(
    document: Any,
    errors: list[dict[str, str]],
) -> dict[str, Any] | None:
    root = _validate_object(
        document,
        "$",
        {
            "schemaVersion",
            "name",
            "description",
            "runtime",
            "inputSchema",
            "capabilities",
            "limits",
            "activation",
        },
        set(),
        errors,
    )
    if root is None:
        return None

    if type(root.get("schemaVersion")) is not int or root.get("schemaVersion") != 1:
        errors.append(
            _issue(
                "$.schemaVersion",
                "unsupported_schema_version",
                "schemaVersion must be the integer 1.",
            )
        )
    name = root.get("name")
    if (
        type(name) is not str
        or len(name) > 64
        or _TOOL_NAME_RE.fullmatch(name) is None
    ):
        errors.append(
            _issue(
                "$.name",
                "invalid_name",
                "Name must use lowercase letters, numbers, and single hyphens, up to 64 characters.",
            )
        )
    description = _validate_description(
        root.get("description"), "$.description", 1_024, errors
    )

    runtime = _validate_object(
        root.get("runtime"),
        "$.runtime",
        {"packageId", "executable", "argv"},
        set(),
        errors,
    )
    package_id: str | None = None
    executable: str | None = None
    arguments: list[str] | None = None
    placeholder_uses: list[tuple[int, str]] = []
    if runtime is not None:
        candidate_package_id = runtime.get("packageId")
        if (
            type(candidate_package_id) is not str
            or _PACKAGE_ID_RE.fullmatch(candidate_package_id) is None
        ):
            errors.append(
                _issue(
                    "$.runtime.packageId",
                    "invalid_package_id",
                    "packageId must be an exact installed CLI package identifier.",
                )
            )
        else:
            package_id = candidate_package_id

        candidate_executable = runtime.get("executable")
        if (
            type(candidate_executable) is not str
            or _EXECUTABLE_RE.fullmatch(candidate_executable) is None
        ):
            errors.append(
                _issue(
                    "$.runtime.executable",
                    "invalid_executable",
                    "Executable must be one recorded entry-point name, without a path.",
                )
            )
        else:
            executable = candidate_executable

        candidate_arguments = runtime.get("argv")
        if type(candidate_arguments) is not list:
            errors.append(
                _issue(
                    "$.runtime.argv",
                    "invalid_type",
                    "argv must be an array of arguments.",
                )
            )
        elif len(candidate_arguments) > MAX_ARGUMENTS:
            errors.append(
                _issue(
                    "$.runtime.argv",
                    "too_many_items",
                    f"argv exceeds the {MAX_ARGUMENTS} argument limit.",
                )
            )
        else:
            parsed_arguments: list[str] = []
            total_argument_bytes = 0
            arguments_valid = True
            for index, argument in enumerate(candidate_arguments):
                argument_path = f"$.runtime.argv[{index}]"
                if type(argument) is not str:
                    errors.append(
                        _issue(argument_path, "invalid_type", "Argument must be a string.")
                    )
                    arguments_valid = False
                    continue
                argument_bytes = len(argument.encode("utf-8"))
                total_argument_bytes += argument_bytes
                if not argument or argument_bytes > MAX_ARGUMENT_BYTES:
                    errors.append(
                        _issue(
                            argument_path,
                            "invalid_argument_length",
                            f"Argument must contain 1 to {MAX_ARGUMENT_BYTES} UTF-8 bytes.",
                        )
                    )
                    arguments_valid = False
                    continue
                if any(
                    ord(character) < 32 or ord(character) == 127
                    for character in argument
                ):
                    errors.append(
                        _issue(
                            argument_path,
                            "invalid_control_character",
                            "Argument contains a control character.",
                        )
                    )
                    arguments_valid = False
                    continue
                placeholder = _PLACEHOLDER_RE.fullmatch(argument)
                if placeholder is not None:
                    placeholder_uses.append((index, placeholder.group(1)))
                elif "{" in argument or "}" in argument:
                    errors.append(
                        _issue(
                            argument_path,
                            "invalid_placeholder",
                            "Placeholders must occupy the whole argument as {input.name}.",
                        )
                    )
                    arguments_valid = False
                    continue
                parsed_arguments.append(argument)
            if total_argument_bytes > MAX_ARGUMENT_TOTAL_BYTES:
                errors.append(
                    _issue(
                        "$.runtime.argv",
                        "argv_too_large",
                        f"argv exceeds {MAX_ARGUMENT_TOTAL_BYTES} UTF-8 bytes.",
                    )
                )
                arguments_valid = False
            if arguments_valid:
                arguments = parsed_arguments

    input_schema = _validate_object(
        root.get("inputSchema"),
        "$.inputSchema",
        {"type", "properties", "required"},
        {"additionalProperties"},
        errors,
    )
    normalized_properties: dict[str, dict[str, str]] | None = None
    normalized_required: list[str] | None = None
    if input_schema is not None:
        if input_schema.get("type") != "object":
            errors.append(
                _issue(
                    "$.inputSchema.type",
                    "invalid_input_schema",
                    "inputSchema.type must be object.",
                )
            )
        properties = input_schema.get("properties")
        if type(properties) is not dict:
            errors.append(
                _issue(
                    "$.inputSchema.properties",
                    "invalid_type",
                    "properties must be an object.",
                )
            )
        elif len(properties) > MAX_INPUT_PROPERTIES:
            errors.append(
                _issue(
                    "$.inputSchema.properties",
                    "too_many_properties",
                    f"properties exceeds the {MAX_INPUT_PROPERTIES} item limit.",
                )
            )
        else:
            parsed_properties: dict[str, dict[str, str]] = {}
            for property_name in sorted(properties):
                property_path = f"$.inputSchema.properties.{property_name}"
                if (
                    type(property_name) is not str
                    or _INPUT_NAME_RE.fullmatch(property_name) is None
                ):
                    errors.append(
                        _issue(
                            property_path,
                            "invalid_input_name",
                            "Input names must use lowercase letters, numbers, and underscores.",
                        )
                    )
                    continue
                property_schema = _validate_object(
                    properties[property_name],
                    property_path,
                    {"type"},
                    {"description"},
                    errors,
                )
                if property_schema is None:
                    continue
                if property_schema.get("type") != "string":
                    errors.append(
                        _issue(
                            f"{property_path}.type",
                            "unsupported_input_type",
                            "TOOL.yaml schema v1 supports string inputs only.",
                        )
                    )
                    continue
                normalized_property = {"type": "string"}
                if "description" in property_schema:
                    property_description = _validate_description(
                        property_schema.get("description"),
                        f"{property_path}.description",
                        512,
                        errors,
                    )
                    if property_description is not None:
                        normalized_property["description"] = property_description
                parsed_properties[property_name] = normalized_property
            normalized_properties = parsed_properties

        required = input_schema.get("required")
        if type(required) is not list:
            errors.append(
                _issue(
                    "$.inputSchema.required",
                    "invalid_type",
                    "required must be an array.",
                )
            )
        elif len(required) > MAX_INPUT_PROPERTIES:
            errors.append(
                _issue(
                    "$.inputSchema.required",
                    "too_many_items",
                    f"required exceeds the {MAX_INPUT_PROPERTIES} item limit.",
                )
            )
        else:
            parsed_required: list[str] = []
            seen_required: set[str] = set()
            for index, required_name in enumerate(required):
                required_path = f"$.inputSchema.required[{index}]"
                if type(required_name) is not str:
                    errors.append(
                        _issue(
                            required_path,
                            "invalid_type",
                            "Required name must be a string.",
                        )
                    )
                elif required_name in seen_required:
                    errors.append(
                        _issue(
                            required_path,
                            "duplicate_item",
                            f"Duplicate required name: {required_name}",
                        )
                    )
                else:
                    seen_required.add(required_name)
                    parsed_required.append(required_name)
            normalized_required = sorted(parsed_required)

        additional_properties = input_schema.get("additionalProperties", False)
        if type(additional_properties) is not bool:
            errors.append(
                _issue(
                    "$.inputSchema.additionalProperties",
                    "invalid_type",
                    "additionalProperties must be false.",
                )
            )
        elif additional_properties:
            errors.append(
                _issue(
                    "$.inputSchema.additionalProperties",
                    "additional_properties_denied",
                    "Additional input properties are not supported.",
                )
            )

    if normalized_properties is not None and normalized_required is not None:
        property_names = set(normalized_properties)
        required_names = set(normalized_required)
        if property_names != required_names:
            errors.append(
                _issue(
                    "$.inputSchema.required",
                    "required_mismatch",
                    "Every declared input property must be required, with no extra names.",
                )
            )
        if arguments is not None:
            placeholder_names = {name for _, name in placeholder_uses}
            for index, placeholder_name in placeholder_uses:
                if placeholder_name not in property_names:
                    errors.append(
                        _issue(
                            f"$.runtime.argv[{index}]",
                            "unknown_placeholder",
                            f"Placeholder does not match an input property: {placeholder_name}",
                        )
                    )
            for property_name in sorted(property_names.difference(placeholder_names)):
                errors.append(
                    _issue(
                        f"$.inputSchema.properties.{property_name}",
                        "unused_input",
                        f"Input property is not used by argv: {property_name}",
                    )
                )

    capabilities = _validate_object(
        root.get("capabilities"),
        "$.capabilities",
        {"network", "filesystem", "environment"},
        set(),
        errors,
    )
    normalized_read: list[str] | None = None
    normalized_write: list[str] | None = None
    if capabilities is not None:
        network = capabilities.get("network")
        if type(network) is not bool:
            errors.append(
                _issue(
                    "$.capabilities.network",
                    "invalid_type",
                    "network must be false.",
                )
            )
        elif network:
            errors.append(
                _issue(
                    "$.capabilities.network",
                    "network_denied",
                    "Network access is not supported.",
                )
            )

        filesystem = _validate_object(
            capabilities.get("filesystem"),
            "$.capabilities.filesystem",
            {"read", "write"},
            set(),
            errors,
        )
        if filesystem is not None:
            normalized_read = _validate_enum_list(
                filesystem.get("read"),
                "$.capabilities.filesystem.read",
                ("input",),
                errors,
            )
            normalized_write = _validate_enum_list(
                filesystem.get("write"),
                "$.capabilities.filesystem.write",
                ("scratch", "artifacts"),
                errors,
            )

        environment = capabilities.get("environment")
        if type(environment) is not list:
            errors.append(
                _issue(
                    "$.capabilities.environment",
                    "invalid_type",
                    "environment must be an empty array.",
                )
            )
        elif environment:
            errors.append(
                _issue(
                    "$.capabilities.environment",
                    "environment_denied",
                    "Environment-variable access is not supported.",
                )
            )

    limits = _validate_object(
        root.get("limits"),
        "$.limits",
        {"timeoutSeconds", "outputBytes"},
        set(),
        errors,
    )
    timeout_seconds: int | None = None
    output_bytes: int | None = None
    if limits is not None:
        timeout_seconds = _validate_integer(
            limits.get("timeoutSeconds"),
            "$.limits.timeoutSeconds",
            1,
            MAX_TIMEOUT_SECONDS,
            errors,
        )
        output_bytes = _validate_integer(
            limits.get("outputBytes"),
            "$.limits.outputBytes",
            1,
            MAX_OUTPUT_BYTES,
            errors,
        )

    if root.get("activation") != "disabled":
        errors.append(
            _issue(
                "$.activation",
                "activation_denied",
                "Tool proposals must remain disabled.",
            )
        )
    if errors:
        return None

    assert type(name) is str
    assert description is not None
    assert package_id is not None
    assert executable is not None
    assert arguments is not None
    assert normalized_properties is not None
    assert normalized_required is not None
    assert normalized_read is not None
    assert normalized_write is not None
    assert timeout_seconds is not None
    assert output_bytes is not None
    return {
        "schemaVersion": 1,
        "name": name,
        "description": description,
        "runtime": {
            "packageId": package_id,
            "executable": executable,
            "argv": arguments,
        },
        "inputSchema": {
            "type": "object",
            "properties": normalized_properties,
            "required": normalized_required,
            "additionalProperties": False,
        },
        "capabilities": {
            "network": False,
            "filesystem": {
                "read": normalized_read,
                "write": normalized_write,
            },
            "environment": [],
        },
        "limits": {
            "timeoutSeconds": timeout_seconds,
            "outputBytes": output_bytes,
        },
        "activation": "disabled",
    }


def _package_summary(package: dict[str, Any]) -> dict[str, Any]:
    return {
        field: package.get(field)
        for field in (
            "packageId",
            "kind",
            "spec",
            "name",
            "version",
            "isolation",
            "allowScripts",
        )
    }


def _resolve_dependency(
    normalized: dict[str, Any],
    packages: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    runtime = normalized["runtime"]
    package_id = runtime["packageId"]
    executable_name = runtime["executable"]
    matches = [
        package
        for package in packages
        if type(package) is dict and package.get("packageId") == package_id
    ]
    if not matches:
        return (
            {
                "status": "package_missing",
                "packageId": package_id,
                "message": "The referenced CLI package is not installed.",
            },
            [],
        )
    if len(matches) != 1:
        return (
            {
                "status": "package_ambiguous",
                "packageId": package_id,
                "message": "Multiple CLI package records use this identifier.",
            },
            [],
        )

    package = matches[0]
    executable_entries = package.get("executables")
    if type(executable_entries) is not list:
        return (
            {
                "status": "package_invalid",
                "packageId": package_id,
                "message": "The CLI package has an invalid executable record.",
            },
            [],
        )
    exact_matches = [
        entry
        for entry in executable_entries
        if type(entry) is dict and entry.get("name") == executable_name
    ]
    available = sorted(
        {
            entry.get("name")
            for entry in executable_entries
            if type(entry) is dict and type(entry.get("name")) is str
        }
    )
    if not exact_matches:
        return (
            {
                "status": "executable_missing",
                "packageId": package_id,
                "executable": executable_name,
                "availableExecutables": available,
                "message": "The executable is not a recorded entry point for this package.",
            },
            [],
        )
    if len(exact_matches) != 1:
        return (
            {
                "status": "executable_ambiguous",
                "packageId": package_id,
                "executable": executable_name,
                "message": "The CLI package contains duplicate executable records.",
            },
            [],
        )

    entry = exact_matches[0]
    entry_path = entry.get("path")
    install_dir = package.get("installDir")
    if type(entry_path) is not str or type(install_dir) is not str:
        return (
            {
                "status": "executable_invalid",
                "packageId": package_id,
                "executable": executable_name,
                "message": "The recorded executable path is invalid.",
            },
            [],
        )
    target = Path(entry_path)
    payload = Path(install_dir)
    try:
        resolved_target = target.resolve(strict=True)
        resolved_payload = payload.resolve(strict=True)
        contained = resolved_target.is_relative_to(resolved_payload)
    except (OSError, RuntimeError):
        contained = False
        resolved_target = target
    if (
        target.name != executable_name
        or not contained
        or not resolved_target.is_file()
    ):
        return (
            {
                "status": "executable_invalid",
                "packageId": package_id,
                "executable": executable_name,
                "message": "The recorded executable target failed containment or file checks.",
            },
            [],
        )

    warnings: list[dict[str, str]] = []
    if package.get("allowScripts") is True:
        warnings.append(
            _issue(
                "$.runtime.packageId",
                "lifecycle_scripts_allowed",
                "This npm package was installed with lifecycle scripts enabled.",
            )
        )
    return (
        {
            "status": "resolved",
            "package": _package_summary(package),
            "executable": executable_name,
            "message": "The package and exact executable record were found.",
        },
        warnings,
    )


def _lint_tool_manifest(
    path: str,
    raw_manifest: bytes,
    packages: list[dict[str, Any]],
) -> dict[str, Any]:
    preview = _preview_shell(path, raw_manifest)
    errors: list[dict[str, str]] = preview["errors"]
    if len(raw_manifest) > MAX_TOOL_MANIFEST_BYTES:
        errors.append(
            _issue(
                "$",
                "manifest_too_large",
                f"TOOL.yaml exceeds the {MAX_TOOL_MANIFEST_BYTES} byte limit.",
            )
        )
        return preview
    try:
        text = raw_manifest.decode("utf-8")
    except UnicodeDecodeError:
        errors.append(_issue("$", "invalid_utf8", "TOOL.yaml must be UTF-8."))
        return preview
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    try:
        tokens = list(yaml.scan(text, Loader=_StrictSafeLoader))
    except (yaml.YAMLError, UnicodeError) as exc:
        errors.append(_issue("$", "invalid_yaml", f"Invalid YAML: {exc}"))
        return preview
    for token in tokens:
        if isinstance(token, AnchorToken):
            errors.append(_issue("$", "yaml_anchor", "YAML anchors are not allowed."))
            return preview
        if isinstance(token, AliasToken):
            errors.append(_issue("$", "yaml_alias", "YAML aliases are not allowed."))
            return preview
        if isinstance(token, TagToken):
            errors.append(
                _issue("$", "yaml_tag", "Explicit YAML tags are not allowed.")
            )
            return preview
        if isinstance(token, ScalarToken) and token.value == "<<":
            errors.append(
                _issue("$", "yaml_merge", "YAML merge keys are not allowed.")
            )
            return preview

    try:
        document = yaml.load(text, Loader=_StrictSafeLoader)
    except _DuplicateKeyError as exc:
        errors.append(_issue("$", "duplicate_key", str(exc)))
        return preview
    except _NonStringKeyError as exc:
        errors.append(_issue("$", "non_string_key", str(exc)))
        return preview
    except ComposerError as exc:
        errors.append(
            _issue(
                "$",
                "multiple_documents",
                f"Expected one YAML document: {exc}",
            )
        )
        return preview
    except (yaml.YAMLError, RecursionError) as exc:
        errors.append(_issue("$", "invalid_yaml", f"Invalid YAML: {exc}"))
        return preview

    try:
        node_count, depth = _measure_yaml_tree(document)
    except RecursionError:
        errors.append(
            _issue("$", "manifest_too_deep", "TOOL.yaml nesting is too deep.")
        )
        return preview
    if depth > MAX_YAML_DEPTH:
        errors.append(
            _issue(
                "$",
                "manifest_too_deep",
                f"TOOL.yaml exceeds the depth limit of {MAX_YAML_DEPTH}.",
            )
        )
        return preview
    if node_count > MAX_YAML_NODES:
        errors.append(
            _issue(
                "$",
                "manifest_too_complex",
                f"TOOL.yaml exceeds the {MAX_YAML_NODES} node limit.",
            )
        )
        return preview

    normalized = _validate_manifest_schema(document, errors)
    if normalized is None:
        return preview
    canonical = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    dependency, warnings = _resolve_dependency(normalized, packages)
    resolved = dependency["status"] == "resolved"
    blockers = [] if resolved else ["dependency_unresolved"]
    blockers.extend(
        [
            "approval_registry_unavailable",
            "execution_worker_unavailable",
        ]
    )
    return {
        **preview,
        "normalizedSha256": hashlib.sha256(canonical).hexdigest(),
        "valid": True,
        "resolved": resolved,
        "warnings": warnings,
        "normalized": normalized,
        "dependency": dependency,
        "commandPreview": [
            normalized["runtime"]["executable"],
            *normalized["runtime"]["argv"],
        ],
        "blockers": blockers,
    }


def _mark_duplicate_tool_names(manifests: list[dict[str, Any]]) -> None:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for manifest in manifests:
        normalized = manifest.get("normalized")
        if manifest.get("valid") is not True or type(normalized) is not dict:
            continue
        name = normalized.get("name")
        if type(name) is str:
            by_name.setdefault(name, []).append(manifest)
    for name, matches in by_name.items():
        if len(matches) < 2:
            continue
        for manifest in matches:
            manifest["valid"] = False
            manifest["errors"].append(
                _issue(
                    "$.name",
                    "duplicate_tool_name",
                    f"Tool name is duplicated in this archive: {name}",
                )
            )
            if "manifest_invalid" not in manifest["blockers"]:
                manifest["blockers"].insert(0, "manifest_invalid")


def preview_tool_manifest_archive(
    filename: str,
    content_base64: str,
    *,
    packages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        _package_id_from_filename(filename)
        payload = _decode_archive(content_base64)
    except SkillPackageError as exc:
        raise ToolManifestArchiveError(str(exc)) from exc
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as exc:
        raise ToolManifestArchiveError(
            "Tool proposal must be a valid .skill or .zip archive"
        ) from exc

    manifest_payloads: list[tuple[str, bytes]] = []
    with archive:
        infos = archive.infolist()
        file_infos = [info for info in infos if not info.is_dir()]
        if not file_infos:
            raise ToolManifestArchiveError("Tool proposal archive is empty")
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ToolManifestArchiveError(
                f"Tool proposal exceeds the {MAX_ARCHIVE_ENTRIES} entry limit"
            )

        normalized_paths: dict[str, str] = {}
        total_bytes = 0
        manifest_infos: list[tuple[str, zipfile.ZipInfo]] = []
        try:
            for info in infos:
                member_path = _safe_member_path(info)
                member_name = str(member_path)
                key = member_name.casefold()
                if key in normalized_paths:
                    raise ToolManifestArchiveError(
                        f"Archive contains duplicate paths: {member_name}"
                    )
                normalized_paths[key] = member_name
                if info.is_dir():
                    continue
                total_bytes += info.file_size
                if total_bytes > MAX_EXPANDED_BYTES:
                    raise ToolManifestArchiveError(
                        f"Tool proposal exceeds the {MAX_EXPANDED_BYTES} byte expanded limit"
                    )
                if member_path.name == "TOOL.yaml":
                    manifest_infos.append((member_name, info))
        except SkillPackageError as exc:
            raise ToolManifestArchiveError(str(exc)) from exc

        if not manifest_infos:
            raise ToolManifestArchiveError(
                "Archive contains no exact-case TOOL.yaml files"
            )
        if len(manifest_infos) > MAX_TOOL_MANIFESTS:
            raise ToolManifestArchiveError(
                f"Archive exceeds the {MAX_TOOL_MANIFESTS} TOOL.yaml limit"
            )
        try:
            for member_name, info in sorted(
                manifest_infos,
                key=lambda item: item[0],
            ):
                manifest_payloads.append((member_name, archive.read(info)))
        except (OSError, RuntimeError, zipfile.BadZipFile) as exc:
            raise ToolManifestArchiveError(
                "TOOL.yaml failed archive integrity checks"
            ) from exc

    installed_packages = list_cli_packages() if packages is None else packages
    if type(installed_packages) is not list:
        raise ToolManifestArchiveError("CLI package inventory is invalid")
    manifests = [
        _lint_tool_manifest(path, raw_manifest, installed_packages)
        for path, raw_manifest in manifest_payloads
    ]
    _mark_duplicate_tool_names(manifests)
    return {
        "filename": Path(filename).name,
        "archiveSha256": hashlib.sha256(payload).hexdigest(),
        "fileCount": len(file_infos),
        "totalBytes": total_bytes,
        "manifestCount": len(manifests),
        "valid": all(manifest["valid"] for manifest in manifests),
        "resolved": all(manifest["resolved"] for manifest in manifests),
        "executionEligible": False,
        "manifests": manifests,
    }


__all__ = [
    "MAX_TOOL_MANIFEST_BYTES",
    "ToolManifestArchiveError",
    "preview_tool_manifest_archive",
]
