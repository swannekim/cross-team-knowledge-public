"""Offline validation of the Copilot manifests against the rules in arch_b_broker.constants
(declarative agent v1.8, API plugin v2.4, MCP tools/list). All rules: verify against current docs.

Each validator returns a list of human-readable errors (empty when valid).
"""
from __future__ import annotations

import re

from . import constants as k

AUTH_TYPES = ("None", "OAuthPluginVault", "ApiKeyPluginVault")
RUNTIME_TYPES = ("OpenApi", "LocalPlugin", "RemoteMCPServer")
MCP_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _text(doc: dict, key: str, max_chars: int, errors: list, required: bool = True) -> None:
    if key not in doc:
        if required:
            errors.append(f"{key}: required")
        return
    value = doc[key]
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key}: non-empty string required")
    elif len(value) > max_chars:
        errors.append(f"{key}: longer than {max_chars} characters")


def validate_declarative_agent(doc) -> list:
    if not isinstance(doc, dict):
        return ["declarative agent must be a JSON object"]
    errors = [f"{key}: unrecognised property (document invalid)" for key in doc if key not in k.DA_ALLOWED_KEYS]
    errors += [f"{key}: required" for key in k.DA_REQUIRED_KEYS if key not in doc]
    if "version" in doc and doc["version"] != k.DECLARATIVE_AGENT_VERSION:
        errors.append(f"version: must be {k.DECLARATIVE_AGENT_VERSION}")
    if "$schema" in doc and doc["$schema"] != k.DECLARATIVE_AGENT_SCHEMA:
        errors.append(f"$schema: must be {k.DECLARATIVE_AGENT_SCHEMA}")
    _text(doc, "name", k.DA_NAME_MAX_CHARS, errors, required=False)
    _text(doc, "description", k.DA_DESCRIPTION_MAX_CHARS, errors, required=False)
    _text(doc, "instructions", k.DA_INSTRUCTIONS_MAX_CHARS, errors, required=False)
    starters = doc.get("conversation_starters")
    if starters is not None:
        if not isinstance(starters, list) or len(starters) > k.DA_CONVERSATION_STARTERS_MAX:
            errors.append(f"conversation_starters: list of at most {k.DA_CONVERSATION_STARTERS_MAX}")
        else:
            for i, starter in enumerate(starters):
                if not isinstance(starter, dict) or not isinstance(starter.get("text"), str) \
                        or set(starter) - {"title", "text"}:
                    errors.append(f"conversation_starters[{i}]: object with 'text' (and optional 'title') only")
    actions = doc.get("actions")
    if actions is not None:
        if not isinstance(actions, list) or not k.DA_ACTIONS_MIN <= len(actions) <= k.DA_ACTIONS_MAX:
            errors.append(f"actions: list of {k.DA_ACTIONS_MIN}-{k.DA_ACTIONS_MAX} entries")
        else:
            for i, action in enumerate(actions):
                if not isinstance(action, dict) or set(action) != {"id", "file"} \
                        or not all(isinstance(action[f], str) and action[f] for f in ("id", "file")):
                    errors.append(f"actions[{i}]: must be exactly {{id, file}} strings")
    capabilities = doc.get("capabilities")
    if capabilities is not None:
        if not isinstance(capabilities, list):
            errors.append("capabilities: list required")
        else:
            for i, capability in enumerate(capabilities):
                if not isinstance(capability, dict) or not isinstance(capability.get("name"), str):
                    errors.append(f"capabilities[{i}]: object with 'name' required")
                elif capability["name"] == "GraphConnectors":
                    errors += _graph_connector_errors(capability, i)
    overrides = doc.get("behavior_overrides")
    if overrides is not None:
        special = overrides.get("special_instructions", {}) if isinstance(overrides, dict) else None
        if not isinstance(special, dict) or not isinstance(special.get("discourage_model_knowledge", False), bool):
            errors.append("behavior_overrides.special_instructions.discourage_model_knowledge: boolean required")
    disclaimer = doc.get("disclaimer")
    if disclaimer is not None:
        if not isinstance(disclaimer, dict) or set(disclaimer) != {"text"}:
            errors.append("disclaimer: object with only 'text'")
        else:
            _text(disclaimer, "text", k.DA_DISCLAIMER_MAX_CHARS, errors)
    return errors


def _graph_connector_errors(capability: dict, index: int) -> list:
    from arch_a_connector.validators import validate_connection
    connections = capability.get("connections")
    if connections is None:
        return []  # all connections in the tenant
    if not isinstance(connections, list) or not connections:
        return [f"capabilities[{index}].connections: non-empty list required"]
    errors = []
    for j, conn in enumerate(connections):
        where = f"capabilities[{index}].connections[{j}]"
        if not isinstance(conn, dict) or not isinstance(conn.get("connection_id"), str):
            errors.append(f"{where}.connection_id: required")
            continue
        errors += [f"{where}: {e}" for e in validate_connection({"id": conn["connection_id"], "name": "x"})]
        if "additional_search_terms" in conn and not isinstance(conn["additional_search_terms"], str):
            errors.append(f"{where}.additional_search_terms: string")
    return errors


def operation_ids(openapi: dict) -> set:
    return {op.get("operationId") for path in openapi.get("paths", {}).values() for op in path.values()
            if isinstance(op, dict)}


def validate_api_plugin(doc, *, openapi=None, mcp_tools=None) -> list:
    if not isinstance(doc, dict):
        return ["plugin manifest must be a JSON object"]
    errors = [f"{key}: unrecognised property" for key in doc if key not in k.PLUGIN_ALLOWED_KEYS]
    errors += [f"{key}: required" for key in k.PLUGIN_REQUIRED_KEYS if key not in doc]
    if doc.get("schema_version") != k.API_PLUGIN_SCHEMA_VERSION:
        errors.append(f"schema_version: must be {k.API_PLUGIN_SCHEMA_VERSION}")
    if "namespace" in doc and not re.match(k.PLUGIN_NAMESPACE_PATTERN, str(doc["namespace"])):
        errors.append(f"namespace: must match {k.PLUGIN_NAMESPACE_PATTERN}")
    _text(doc, "name_for_human", 10_000, errors, required=False)
    _text(doc, "description_for_human", k.PLUGIN_DESCRIPTION_FOR_HUMAN_MAX_CHARS, errors, required=False)
    functions = doc.get("functions", [])
    names = []
    for i, function in enumerate(functions if isinstance(functions, list) else []):
        name = function.get("name") if isinstance(function, dict) else None
        if not isinstance(name, str) or not re.match(k.PLUGIN_FUNCTION_NAME_PATTERN, name):
            errors.append(f"functions[{i}].name: must match {k.PLUGIN_FUNCTION_NAME_PATTERN}")
            continue
        if name in names:
            errors.append(f"functions[{i}].name: duplicate {name}")
        names.append(name)
        capabilities = function.get("capabilities", {})
        handling = capabilities.get("security_info", {}).get("data_handling", [])
        if not handling or not set(handling) <= set(k.PLUGIN_DATA_HANDLING_VALUES):
            errors.append(f"functions[{i}].capabilities.security_info.data_handling: one or more of "
                          f"{k.PLUGIN_DATA_HANDLING_VALUES}")
        semantics = capabilities.get("response_semantics")
        if semantics is not None:
            if not str(semantics.get("data_path", "")).startswith("$"):
                errors.append(f"functions[{i}].response_semantics.data_path: JSONPath required")
            for prop, path in (semantics.get("properties") or {}).items():
                if not str(path).startswith("$"):
                    errors.append(f"functions[{i}].response_semantics.properties.{prop}: JSONPath required")
    runtimes = doc.get("runtimes", [])
    for i, runtime in enumerate(runtimes if isinstance(runtimes, list) else []):
        where = f"runtimes[{i}]"
        rtype = runtime.get("type")
        if rtype not in RUNTIME_TYPES:
            errors.append(f"{where}.type: one of {RUNTIME_TYPES}")
        auth = runtime.get("auth", {})
        if auth.get("type") not in AUTH_TYPES:
            errors.append(f"{where}.auth.type: one of {AUTH_TYPES}")
        elif auth["type"] != "None" and not auth.get("reference_id"):
            errors.append(f"{where}.auth.reference_id: required for {auth['type']}")
        run_for = runtime.get("run_for_functions", [])
        unknown = sorted(set(run_for) - set(names))
        if not run_for or unknown:
            errors.append(f"{where}.run_for_functions: must be a non-empty subset of functions (unknown: {unknown})")
        spec = runtime.get("spec", {})
        if not isinstance(spec.get("url"), str) or not spec["url"]:
            errors.append(f"{where}.spec.url: required")
        if rtype == "RemoteMCPServer":
            if not str(spec.get("url", "")).startswith("https://"):
                errors.append(f"{where}.spec.url: https MCP endpoint required")
            if not isinstance(spec.get("mcp_tool_description", {}).get("file"), str):
                errors.append(f"{where}.spec.mcp_tool_description.file: required")
            if mcp_tools is not None:
                tool_names = {t.get("name") for t in mcp_tools.get("tools", [])}
                missing = sorted(set(run_for) - tool_names)
                if missing:
                    errors.append(f"{where}: functions without MCP tool: {missing}")
        if rtype == "OpenApi" and openapi is not None:
            ids = operation_ids(openapi)
            if set(names) != ids:
                errors.append(f"functions {sorted(names)} must equal OpenAPI operationIds {sorted(ids)}")
    return errors


def validate_mcp_tools(doc) -> list:
    if not isinstance(doc, dict) or not isinstance(doc.get("tools"), list) or not doc["tools"]:
        return ["mcp tools: object with non-empty 'tools' list required (tools/list result)"]
    errors, seen = [], set()
    for i, tool in enumerate(doc["tools"]):
        if not isinstance(tool, dict):
            errors.append(f"tools[{i}]: object required")
            continue
        errors += [f"tools[{i}].{key}: required" for key in k.MCP_TOOL_REQUIRED_KEYS if key not in tool]
        errors += [f"tools[{i}].{key}: not a tools/list field" for key in tool if key not in k.MCP_TOOL_ALLOWED_KEYS]
        name = tool.get("name")
        if not isinstance(name, str) or not MCP_TOOL_NAME.match(name):
            errors.append(f"tools[{i}].name: invalid")
        elif name in seen:
            errors.append(f"tools[{i}].name: duplicate")
        seen.add(name)
        if "description" in tool and not (isinstance(tool["description"], str) and tool["description"].strip()):
            errors.append(f"tools[{i}].description: non-empty string")
        schema = tool.get("inputSchema")
        if "inputSchema" in tool and (not isinstance(schema, dict) or schema.get("type") != "object"):
            errors.append(f"tools[{i}].inputSchema: JSON Schema object with type 'object'")
    return errors
