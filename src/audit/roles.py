"""角色与权限：按角色编码授予，操作前统一鉴权。"""

from .repository import Repository

CLINIC_ROLES = {"ROLE-ATTENDING", "ROLE-RESIDENT"}


class Roles:
    def __init__(self, repo: Repository):
        self.roles = repo.roles

    def permissions_of(self, role_code: str) -> set[str]:
        role = self.roles.get(role_code)
        if role is None:
            raise KeyError(f"未知角色 {role_code}")
        return set(role["permissions"])

    def can(self, role_code: str, permission: str) -> bool:
        return permission in self.permissions_of(role_code)

    def require(self, role_code: str, permission: str) -> None:
        if not self.can(role_code, permission):
            raise PermissionError(f"角色 {role_code} 缺少权限 {permission}")
