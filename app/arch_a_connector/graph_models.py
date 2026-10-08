"""Builders that produce Microsoft Graph v1.0 external-connector JSON shapes.

* externalConnection  -> POST  /external/connections
* schema              -> PATCH /external/connections/{id}/schema
* externalItem        -> PUT   /external/connections/{id}/items/{itemId}
"""
from __future__ import annotations

from common.refs import access_request_url

BASE_TYPE = "microsoft.graph.externalItem"


def build_connection(connection_id: str, name: str, description: str) -> dict:
    return {"id": connection_id, "name": name, "description": description}


def schema_property(name: str, type_: str, *, searchable: bool = False, queryable: bool = False,
                    retrievable: bool = False, refinable: bool = False, labels=()) -> dict:
    return {"name": name, "type": type_, "isSearchable": searchable, "isQueryable": queryable,
            "isRetrievable": retrievable, "isRefinable": refinable, "labels": list(labels)}


def build_schema() -> dict:
    """Schema for derived knowledge items. 'url' points to the broker access-request page and 'containerName' is the
    sharing-contract display name - never an original location."""
    return {
        "baseType": BASE_TYPE,
        "properties": [
            schema_property("title", "String", searchable=True, queryable=True, retrievable=True, labels=["title"]),
            schema_property("url", "String", retrievable=True, labels=["url"]),
            schema_property("lastModifiedDateTime", "DateTime", queryable=True, retrievable=True, refinable=True,
                            labels=["lastModifiedDateTime"]),
            schema_property("containerName", "String", queryable=True, retrievable=True, refinable=True,
                            labels=["containerName"]),
            schema_property("sourceTeam", "String", queryable=True, retrievable=True, refinable=True),
            schema_property("sensitivity", "String", queryable=True, retrievable=True, refinable=True),
            schema_property("derivativeType", "String", queryable=True, retrievable=True, refinable=True),
            schema_property("contractId", "String", queryable=True, retrievable=True, refinable=True),
            schema_property("validUntil", "DateTime", queryable=True, retrievable=True, refinable=True),
            schema_property("sourceFingerprint", "String", queryable=True, retrievable=True),
        ],
    }


def acl_entry(type_: str, value: str, access_type: str = "grant") -> dict:
    return {"type": type_, "value": value, "accessType": access_type}


def build_external_item(acl: list, properties: dict, content: str, content_type: str = "text") -> dict:
    return {"acl": list(acl), "properties": dict(properties), "content": {"type": content_type, "value": content}}


def item_url(ref: str) -> str:
    return access_request_url(ref)
