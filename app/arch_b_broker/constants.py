"""Baseline manifest examples and shared broker limits, not live deployment settings.

Every value below must be verified against current Microsoft 365 Copilot / Entra / MCP
documentation before production use ("verify against current docs"). Contoso hosts,
app IDs and the OAuthPluginVault reference intentionally remain reproducible fixtures;
the current provisioning scripts do not resolve that reference into working SSO.
live.py takes tenant, broker ID and public origin from explicit runtime configuration.
Generate a separate tenant-specific package for actual Copilot deployment.

Baseline manifests are regenerated with ``python -m arch_b_broker.manifest_builder``.
Changing versions also requires validator and compatibility checks, not just regeneration.
"""
from __future__ import annotations

VERIFY = "verify against current docs"

# Microsoft 365 Copilot declarative agent manifest (v1.8)
DECLARATIVE_AGENT_SCHEMA = "https://developer.microsoft.com/json-schemas/copilot/declarative-agent/v1.8/schema.json"
DECLARATIVE_AGENT_VERSION = "v1.8"
DA_REQUIRED_KEYS = ("version", "name", "description", "instructions")
DA_ALLOWED_KEYS = ("$schema", "version", "id", "name", "description", "instructions", "capabilities",
                   "conversation_starters", "actions", "behavior_overrides", "disclaimer", "sensitivity_label",
                   "editorial_answers", "worker_agents", "user_overrides")
DA_NAME_MAX_CHARS = 100
DA_DESCRIPTION_MAX_CHARS = 1000
DA_INSTRUCTIONS_MAX_CHARS = 8000
DA_CONVERSATION_STARTERS_MAX = 12
DA_ACTIONS_MIN, DA_ACTIONS_MAX = 1, 10
DA_DISCLAIMER_MAX_CHARS = 500

# Microsoft 365 Copilot API plugin manifest (v2.4)
API_PLUGIN_SCHEMA = "https://developer.microsoft.com/json-schemas/copilot/plugin/v2.4/schema.json"
API_PLUGIN_SCHEMA_VERSION = "v2.4"
PLUGIN_REQUIRED_KEYS = ("schema_version", "name_for_human", "namespace", "description_for_human")
PLUGIN_ALLOWED_KEYS = ("$schema", "schema_version", "name_for_human", "namespace", "description_for_model",
                       "description_for_human", "logo_url", "contact_email", "legal_info_url", "privacy_policy_url",
                       "functions", "runtimes", "capabilities")
PLUGIN_NAMESPACE_PATTERN = r"^[A-Za-z0-9]+$"
PLUGIN_FUNCTION_NAME_PATTERN = r"^[A-Za-z0-9_]+$"
PLUGIN_DESCRIPTION_FOR_HUMAN_MAX_CHARS = 100
PLUGIN_RUNTIME_TYPE = "OpenApi"
PLUGIN_MCP_RUNTIME_TYPE = "RemoteMCPServer"
PLUGIN_AUTH_TYPE = "OAuthPluginVault"
PLUGIN_AUTH_REFERENCE_ID = "${{BROKER_SSO_REFERENCE_ID}}"  # placeholder resolved at provisioning time
PLUGIN_NAMESPACE = "contosoknowledge"
PLUGIN_DATA_HANDLING = ("GetPrivateData",)
PLUGIN_DATA_HANDLING_VALUES = ("GetPublicData", "GetPrivateData", "DataTransform", "ResourceStateUpdate")

# OpenAPI description consumed by the plugin runtime
OPENAPI_VERSION = "3.0.1"

# Model Context Protocol (alternative exposure as an MCP tool server)
MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_NAME = "contoso-knowledge-broker"
MCP_TOOL_REQUIRED_KEYS = ("name", "description", "inputSchema")
MCP_TOOL_ALLOWED_KEYS = ("name", "title", "description", "inputSchema", "outputSchema", "annotations", "_meta")

# Entra ID / broker API
ENTRA_AUTHORITY = "https://login.microsoftonline.com"
BROKER_PUBLIC_URL = "https://broker.contoso.com"
MCP_SERVER_URL = BROKER_PUBLIC_URL + "/mcp"
BROKER_APP_ID_URI = "api://contoso-knowledge-broker"
BROKER_CLIENT_ID = "5e7b0c1d-2f3a-4b5c-8d6e-7f8091a2b3c4"  # fictional app registration (token 'aud' for v2 tokens)
REQUIRED_SCOPE = "Knowledge.Ask"
TOKEN_VERSION_ISSUER = "https://login.microsoftonline.com/{tid}/v2.0"

# Broker operations (OpenAPI operationIds == plugin function names == MCP tool names)
FUNCTION_ASK = "askKnowledge"
FUNCTION_SEARCH = "searchKnowledge"
FUNCTIONS = (FUNCTION_ASK, FUNCTION_SEARCH)

# Microsoft Purview data security (processContent) - see purview.py
PURVIEW_PROCESS_CONTENT_PATH = "/users/{userId}/dataSecurityAndGovernance/processContent"
PURVIEW_ACTIVITY_PROMPT = "uploadText"
PURVIEW_ACTIVITY_RESPONSE = "downloadText"

# Broker behaviour not carried by the sharing contract
MAX_CITATIONS_PER_ANSWER = 3
MIN_RELATIVE_SCORE = 0.5  # secondary citations must score >= 50% of the best hit (saves exfiltration allowance)
MAX_SEARCH_RESULTS = 5
MAX_QUESTION_CHARS = 1000
MAX_REQUEST_BYTES = 16 * 1024
INDEX_CHUNK_MAX_CHARS = 500
EXFILTRATION_WINDOW_HOURS = 24

VERIFY_LIST = {
    "DECLARATIVE_AGENT_SCHEMA / DECLARATIVE_AGENT_VERSION / DA_*": "declarative agent manifest v1.8 schema, keys and limits",
    "API_PLUGIN_SCHEMA / API_PLUGIN_SCHEMA_VERSION / PLUGIN_*": "API plugin manifest v2.4 schema, keys, regexes, runtimes",
    "PLUGIN_AUTH_TYPE / PLUGIN_AUTH_REFERENCE_ID": "OAuthPluginVault (SSO) registration reference id",
    "OPENAPI_VERSION": "OpenAPI versions supported by API plugins",
    "MCP_PROTOCOL_VERSION / MCP_TOOL_*": "Model Context Protocol revision and tools/list format",
    "TOKEN_VERSION_ISSUER / BROKER_CLIENT_ID": "Entra v2 access-token iss/aud claim formats",
    "PURVIEW_*": "Microsoft Purview processContent API path, activities and response shape",
}
