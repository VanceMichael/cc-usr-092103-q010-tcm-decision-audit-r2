"""审计系统统一异常。"""


class AuditError(Exception):
    """系统内所有可预期错误的基类。"""


class ValidationError(AuditError):
    """资料或请求格式不合法。"""


class DirectIdentifierError(ValidationError):
    """请求携带直接身份标识，违反最小化要求。"""


class NotFoundError(AuditError):
    """引用的对象不存在。"""


class ConflictError(AuditError):
    """同一标识出现不同内容，拒绝覆盖。"""


class ConsentError(AuditError):
    """患者授权不足或已撤回。"""


class PermissionDenied(AuditError):
    """机构权限、适用范围或停用条件不允许。"""


class RoleError(PermissionDenied):
    """角色不具备所需权限。"""
