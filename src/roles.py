"""角色与权限。"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import NotFoundError, RoleError, ValidationError


class Roles:
    def __init__(self, permissions: dict):
        self._permissions = {r: frozenset(p) for r, p in permissions.items()}

    @classmethod
    def load(cls, path) -> "Roles":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if "roles" not in data:
            raise ValidationError("角色资料缺少 roles 字段")
        permissions = {}
        for raw in data["roles"]:
            if not isinstance(raw, dict) or not {"id", "name", "permissions"} <= set(raw):
                raise ValidationError(f"角色条目缺少必要字段: {raw!r}")
            permissions[raw["id"]] = raw["permissions"]
        return cls(permissions)

    def permissions(self, role: str) -> frozenset:
        try:
            return self._permissions[role]
        except KeyError:
            raise NotFoundError(f"未知角色: {role}") from None

    def require(self, role: str, permission: str) -> None:
        if permission not in self.permissions(role):
            raise RoleError(f"角色 {role} 不具备权限 {permission}")
