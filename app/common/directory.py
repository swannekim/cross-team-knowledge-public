"""Simulated Entra ID directory: users, userType (Member/Guest), group membership and roles.

Production equivalents: Graph /users/{id}?$select=userType, /users/{id}/transitiveMemberOf or
checkMemberGroups, and the optional `acct` token claim (0 = member, 1 = guest).
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class User:
    id: str
    alias: str
    upn: str
    display_name: str
    user_type: str
    groups: tuple
    roles: tuple

    @property
    def is_guest(self) -> bool:
        return self.user_type.casefold() != "member"


class UnknownUserError(KeyError):
    pass


class Directory:
    def __init__(self, data: dict):
        self._data = copy.deepcopy(data)
        self.tenant_id = self._data["tenantId"]
        self._groups = {g["id"]: g for g in self._data["groups"]}
        self._users = {u["id"]: u for u in self._data["users"]}

    @classmethod
    def load(cls, path) -> "Directory":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    # ------------------------------------------------------------------ lookups
    def _raw_user(self, key: str) -> dict:
        if key in self._users:
            return self._users[key]
        folded = (key or "").casefold()
        for user in self._users.values():
            if folded in (user["alias"].casefold(), user["userPrincipalName"].casefold()):
                return user
        raise UnknownUserError(key)

    def user(self, key: str) -> User:
        raw = self._raw_user(key)
        return User(id=raw["id"], alias=raw["alias"], upn=raw["userPrincipalName"], display_name=raw["displayName"],
                    user_type=raw.get("userType", "Member"), groups=tuple(raw.get("memberOf", [])),
                    roles=tuple(raw.get("roles", [])))

    def find_user(self, key: str) -> Optional[User]:
        try:
            return self.user(key)
        except UnknownUserError:
            return None

    def users(self) -> list:
        return [self.user(uid) for uid in self._users]

    def group_id(self, name_or_id: str) -> str:
        if name_or_id in self._groups:
            return name_or_id
        for gid, group in self._groups.items():
            if group["displayName"].casefold() == name_or_id.casefold():
                return gid
        raise KeyError(name_or_id)

    def group_name(self, group_id: str) -> str:
        return self._groups[group_id]["displayName"]

    def is_member(self, user_key: str, group_id: str) -> bool:
        user = self.find_user(user_key)
        return bool(user) and group_id in user.groups

    def members_of(self, group_id: str) -> list:
        return [self.user(uid) for uid, raw in self._users.items() if group_id in raw.get("memberOf", [])]

    def has_role(self, user_key: str, role: str) -> bool:
        user = self.find_user(user_key)
        return bool(user) and role in user.roles

    # ------------------------------------------------------------------ mutations (simulated admin activity)
    def add_user(self, *, oid: str, alias: str, upn: str, display_name: str, user_type: str = "Member",
                 groups=(), roles=()) -> User:
        self._users[oid] = {"id": oid, "alias": alias, "userPrincipalName": upn, "displayName": display_name,
                            "userType": user_type, "memberOf": list(groups), "roles": list(roles)}
        return self.user(oid)

    def add_member(self, group_id: str, user_key: str) -> None:
        raw = self._raw_user(user_key)
        if group_id not in raw["memberOf"]:
            raw["memberOf"].append(group_id)

    def remove_member(self, group_id: str, user_key: str) -> None:
        raw = self._raw_user(user_key)
        if group_id in raw["memberOf"]:
            raw["memberOf"].remove(group_id)
