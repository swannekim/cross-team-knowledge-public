"""Client/emulator-side validation of Microsoft Graph external connector payloads (v1.0).

ALL platform constraints live in the single CONFIG dict below. Every entry is marked
"verify against current docs": limits and enumerations change over time and must be re-checked
against https://learn.microsoft.com/graph/connecting-external-content-* before production use.
"""
from __future__ import annotations

import json
import re

VERIFY = "verify against current docs"
_DOCS = "https://learn.microsoft.com/graph/api/resources/"

CONFIG = {
    "GRAPH_BASE_URL": {"value": "https://graph.microsoft.com/v1.0", "verify": VERIFY,
                       "source": "Graph REST v1.0 endpoint"},
    "TOKEN_AUTHORITY": {"value": "https://login.microsoftonline.com", "verify": VERIFY,
                        "source": "Microsoft identity platform client-credentials flow"},
    "GRAPH_DEFAULT_SCOPE": {"value": "https://graph.microsoft.com/.default", "verify": VERIFY,
                            "source": "client-credentials scope"},
    "APP_PERMISSIONS": {"value": ["ExternalConnection.ReadWrite.OwnedBy", "ExternalItem.ReadWrite.OwnedBy",
                                  "Sites.Selected"], "verify": VERIFY,
                        "source": "least-privilege application permissions (connector + selected source site)"},
    "CONNECTION_ID_MIN_LEN": {"value": 3, "verify": VERIFY, "source": _DOCS + "externalconnectors-externalconnection"},
    "CONNECTION_ID_MAX_LEN": {"value": 32, "verify": VERIFY, "source": _DOCS + "externalconnectors-externalconnection"},
    "CONNECTION_ID_PATTERN": {"value": r"^[A-Za-z0-9]+$", "verify": VERIFY,
                              "source": _DOCS + "externalconnectors-externalconnection (alphanumeric only)"},
    "CONNECTION_ID_FORBIDDEN_PREFIX": {"value": "Microsoft", "verify": VERIFY,
                                       "source": _DOCS + "externalconnectors-externalconnection"},
    "CONNECTION_ID_RESERVED": {"value": ["Microsoft", "None", "Directory", "Exchange", "ExchangeArchive", "LinkedIn", "Mailbox",
                                         "OneDriveBusiness", "SharePoint", "Teams", "Yammer", "Connectors",
                                         "TaskFabric", "PowerBI", "Assistant", "TopicEngine", "MSFT_All_Connectors"],
                               "verify": VERIFY, "source": _DOCS + "externalconnectors-externalconnection"},
    "CONNECTION_NAME_MAX_LEN": {"value": 128, "verify": VERIFY, "source": "assumed display-name limit"},
    "SCHEMA_BASE_TYPE": {"value": "microsoft.graph.externalItem", "verify": VERIFY,
                         "source": _DOCS + "externalconnectors-schema"},
    "MAX_PROPERTIES": {"value": 128, "verify": VERIFY, "source": _DOCS + "externalconnectors-schema"},
    "PROPERTY_NAME_MAX_LEN": {"value": 32, "verify": VERIFY, "source": _DOCS + "externalconnectors-property"},
    "PROPERTY_NAME_PATTERN": {"value": r"^[A-Za-z0-9]+$", "verify": VERIFY,
                              "source": _DOCS + "externalconnectors-property (alphanumeric only)"},
    "PROPERTY_TYPES": {"value": ["String", "Int64", "Double", "DateTime", "Boolean", "StringCollection",
                                 "Int64Collection", "DoubleCollection", "DateTimeCollection"], "verify": VERIFY,
                       "source": _DOCS + "externalconnectors-property"},
    "SEARCHABLE_TYPES": {"value": ["String", "StringCollection"], "verify": VERIFY,
                         "source": _DOCS + "externalconnectors-property (isSearchable)"},
    "SEARCHABLE_AND_REFINABLE_ALLOWED": {"value": False, "verify": VERIFY,
                                         "source": _DOCS + "externalconnectors-property (isRefinable)"},
    "SEMANTIC_LABELS": {"value": ["title", "url", "createdBy", "lastModifiedBy", "authors", "createdDateTime",
                                  "lastModifiedDateTime", "fileName", "fileExtension", "iconUrl", "containerName",
                                  "containerUrl"], "verify": VERIFY,
                        "source": _DOCS + "externalconnectors-property (labels)"},
    "LABEL_TYPE_RULES": {"value": {"title": ["String"], "url": ["String"], "createdBy": ["String"],
                                   "lastModifiedBy": ["String"], "authors": ["StringCollection"],
                                   "createdDateTime": ["DateTime"], "lastModifiedDateTime": ["DateTime"],
                                   "fileName": ["String"], "fileExtension": ["String"], "iconUrl": ["String"],
                                   "containerName": ["String"], "containerUrl": ["String"]}, "verify": VERIFY,
                         "source": "semantic label type expectations"},
    "ITEM_ID_MAX_LEN": {"value": 128, "verify": VERIFY, "source": _DOCS + "externalconnectors-externalitem"},
    "ITEM_ID_PATTERN": {"value": r"^[A-Za-z0-9._~-]+$", "verify": VERIFY,
                        "source": "URL-safe (RFC 3986 unreserved) item id"},
    "MAX_ITEM_SIZE_BYTES": {"value": 30_000_000, "verify": VERIFY,
                            "source": "Graph connectors API limits page (updated 2026-07-23): max 30 MB of parsed text per "
                                      "item; enforced conservatively (decimal MB) on the whole serialized item"},
    "ACL_TYPES": {"value": ["user", "group", "everyone", "everyoneExceptGuests", "externalGroup"], "verify": VERIFY,
                  "source": _DOCS + "externalconnectors-acl"},
    "ACL_ACCESS_TYPES": {"value": ["grant", "deny"], "verify": VERIFY, "source": _DOCS + "externalconnectors-acl"},
    "ACL_DENY_WINS": {"value": True, "verify": VERIFY, "source": _DOCS + "externalconnectors-acl (deny precedence)"},
    "EVERYONE_ACE_VALUE": {"value": "tenantId", "verify": VERIFY,
                           "source": _DOCS + "externalconnectors-acl: value for everyone / everyoneExceptGuests is the "
                                             "tenant ID (configurable in SyncEngine)"},
    "ACL_REQUIRED": {"value": True, "verify": VERIFY, "source": _DOCS + "externalconnectors-externalitem (acl mandatory)"},
    "CONTENT_TYPES": {"value": ["text", "html"], "verify": VERIFY,
                      "source": _DOCS + "externalconnectors-externalitemcontent"},
    "DATETIME_PATTERN": {"value": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?Z$", "verify": VERIFY,
                         "source": "ISO-8601 UTC (Edm.DateTimeOffset)"},
    "COLLECTION_ODATA_TYPES": {"value": {"StringCollection": "Collection(String)", "Int64Collection": "Collection(Int64)",
                                         "DoubleCollection": "Collection(Double)",
                                         "DateTimeCollection": "Collection(DateTimeOffset)"}, "verify": VERIFY,
                               "source": "@odata.type annotation required for collection values"},
    "SCHEMA_REGISTRATION_METHOD": {"value": "PATCH", "verify": VERIFY,
                                   "source": "PATCH /external/connections/{id}/schema returns 202 + Location"},
}


def cfg(key: str):
    return CONFIG[key]["value"]


class ValidationError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("; ".join(self.errors))


def is_utc_datetime(value) -> bool:
    return isinstance(value, str) and re.match(cfg("DATETIME_PATTERN"), value) is not None


def validate_connection(connection: dict) -> list:
    errors = []
    cid = connection.get("id")
    if not isinstance(cid, str):
        return ["connection.id: required string"]
    if not cfg("CONNECTION_ID_MIN_LEN") <= len(cid) <= cfg("CONNECTION_ID_MAX_LEN"):
        errors.append(f"connection.id: length must be {cfg('CONNECTION_ID_MIN_LEN')}-{cfg('CONNECTION_ID_MAX_LEN')}")
    if not re.match(cfg("CONNECTION_ID_PATTERN"), cid):
        errors.append("connection.id: alphanumeric characters only")
    if cid.casefold().startswith(cfg("CONNECTION_ID_FORBIDDEN_PREFIX").casefold()):
        errors.append("connection.id: must not start with 'Microsoft'")
    if cid.casefold() in {r.casefold() for r in cfg("CONNECTION_ID_RESERVED")}:
        errors.append("connection.id: reserved value")
    name = connection.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("connection.name: required")
    elif len(name) > cfg("CONNECTION_NAME_MAX_LEN"):
        errors.append("connection.name: too long")
    if "description" in connection and not isinstance(connection["description"], str):
        errors.append("connection.description: string")
    return errors


def validate_schema(schema: dict) -> list:
    errors = []
    if schema.get("baseType") != cfg("SCHEMA_BASE_TYPE"):
        errors.append(f"schema.baseType: must be {cfg('SCHEMA_BASE_TYPE')}")
    props = schema.get("properties")
    if not isinstance(props, list) or not props:
        return errors + ["schema.properties: non-empty list required"]
    if len(props) > cfg("MAX_PROPERTIES"):
        errors.append(f"schema.properties: at most {cfg('MAX_PROPERTIES')} properties")
    names, labels_seen = set(), {}
    for prop in props:
        name = prop.get("name")
        where = f"property {name!r}"
        if not isinstance(name, str) or not re.match(cfg("PROPERTY_NAME_PATTERN"), name):
            errors.append(f"{where}: name must be alphanumeric")
        elif len(name) > cfg("PROPERTY_NAME_MAX_LEN"):
            errors.append(f"{where}: name longer than {cfg('PROPERTY_NAME_MAX_LEN')} characters")
        if name in names:
            errors.append(f"{where}: duplicate name")
        names.add(name)
        ptype = prop.get("type")
        if ptype not in cfg("PROPERTY_TYPES"):
            errors.append(f"{where}: unsupported type {ptype!r}")
        for flag in ("isSearchable", "isQueryable", "isRetrievable", "isRefinable"):
            if flag in prop and not isinstance(prop[flag], bool):
                errors.append(f"{where}: {flag} must be boolean")
        if prop.get("isSearchable") and ptype not in cfg("SEARCHABLE_TYPES"):
            errors.append(f"{where}: isSearchable only allowed for {cfg('SEARCHABLE_TYPES')}")
        if prop.get("isSearchable") and prop.get("isRefinable") and not cfg("SEARCHABLE_AND_REFINABLE_ALLOWED"):
            errors.append(f"{where}: cannot be both isSearchable and isRefinable")
        for label in prop.get("labels", []) or []:
            if label not in cfg("SEMANTIC_LABELS"):
                errors.append(f"{where}: unknown semantic label {label!r}")
                continue
            if label in labels_seen:
                errors.append(f"{where}: semantic label {label!r} already used by {labels_seen[label]!r}")
            labels_seen[label] = name
            allowed = cfg("LABEL_TYPE_RULES").get(label)
            if allowed and ptype not in allowed:
                errors.append(f"{where}: label {label!r} requires type {allowed}")
    return errors


def validate_item_id(item_id) -> list:
    if not isinstance(item_id, str) or not item_id:
        return ["item id: required"]
    errors = []
    if len(item_id) > cfg("ITEM_ID_MAX_LEN"):
        errors.append(f"item id: longer than {cfg('ITEM_ID_MAX_LEN')} characters")
    if not re.match(cfg("ITEM_ID_PATTERN"), item_id):
        errors.append("item id: must be URL-safe")
    return errors


_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def validate_acl(acl) -> list:
    if not isinstance(acl, list) or not acl:
        return ["acl: at least one access control entry is required (acl is mandatory on every item)"]
    errors = []
    for i, ace in enumerate(acl):
        if not isinstance(ace, dict):
            errors.append(f"acl[{i}]: object required")
            continue
        if ace.get("type") in ("everyone", "everyoneExceptGuests") and not _GUID.match(str(ace.get("value", ""))):
            errors.append(f"acl[{i}].value: must be the tenant ID for type {ace.get('type')}")
        if ace.get("type") not in cfg("ACL_TYPES"):
            errors.append(f"acl[{i}].type: one of {cfg('ACL_TYPES')}")
        if ace.get("accessType") not in cfg("ACL_ACCESS_TYPES"):
            errors.append(f"acl[{i}].accessType: one of {cfg('ACL_ACCESS_TYPES')}")
        if not isinstance(ace.get("value"), str) or not ace.get("value"):
            errors.append(f"acl[{i}].value: required")
    return errors


def item_size_bytes(item: dict) -> int:
    return len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def item_within_size_limit(item: dict) -> bool:
    """Cheap upper bound first (<= 6 bytes per content char after JSON escaping/UTF-8), exact size otherwise."""
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    value = content.get("value", "") if isinstance(content.get("value", ""), str) else ""
    rest = {k: val for k, val in item.items() if k != "content"}
    upper = 6 * len(value) + item_size_bytes(rest) + 64
    return upper <= cfg("MAX_ITEM_SIZE_BYTES") or item_size_bytes(item) <= cfg("MAX_ITEM_SIZE_BYTES")


def _type_ok(value, ptype: str) -> bool:
    if ptype == "String":
        return isinstance(value, str)
    if ptype == "Int64":
        return isinstance(value, int) and not isinstance(value, bool)
    if ptype == "Double":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if ptype == "DateTime":
        return is_utc_datetime(value)
    if ptype == "Boolean":
        return isinstance(value, bool)
    if ptype.endswith("Collection"):
        base = ptype[: -len("Collection")]
        return isinstance(value, list) and all(_type_ok(v, base) for v in value)
    return False


def validate_item(item: dict, schema=None, item_id=None) -> list:
    errors = []
    if item_id is not None:
        errors.extend(validate_item_id(item_id))
    errors.extend(validate_acl(item.get("acl")))
    content = item.get("content")
    if content is not None:
        if not isinstance(content, dict) or content.get("type") not in cfg("CONTENT_TYPES") \
                or not isinstance(content.get("value"), str):
            errors.append(f"content: {{'type': one of {cfg('CONTENT_TYPES')}, 'value': string}}")
    props = item.get("properties")
    if not isinstance(props, dict):
        errors.append("properties: object required")
        props = {}
    if schema is not None:
        declared = {p["name"]: p["type"] for p in schema.get("properties", [])}
        for key, value in props.items():
            if "@odata.type" in key:
                base = key.split("@", 1)[0]
                expected = cfg("COLLECTION_ODATA_TYPES").get(declared.get(base, ""))
                if expected is None or value != expected:
                    errors.append(f"properties.{key}: unexpected annotation")
                continue
            if key not in declared:
                errors.append(f"properties.{key}: not declared in schema")
            elif not _type_ok(value, declared[key]):
                errors.append(f"properties.{key}: value does not match type {declared[key]}")
            elif declared[key] in cfg("COLLECTION_ODATA_TYPES") and f"{key}@odata.type" not in props:
                errors.append(f"properties.{key}: collection requires '{key}@odata.type' annotation")
    if not item_within_size_limit(item):
        errors.append(f"item size {item_size_bytes(item)} bytes exceeds {cfg('MAX_ITEM_SIZE_BYTES')} bytes")
    return errors
