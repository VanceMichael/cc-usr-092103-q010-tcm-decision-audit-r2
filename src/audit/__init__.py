"""方证辅助诊疗审计系统。

模块划分：

- serde：规范化序列化、摘要、HMAC 化名与稳定分桶
- repository：领域资料装载
- terminology / organizations / roles：方证术语、机构层级、角色权限
- consents / registry：患者授权快照、模型版本登记与停用状态
- minimize：推理请求最少必要校验
- inference：版本化、确定性的方证推理
- events：只增哈希链事件
- engine：审计引擎（路由、重放、灰度/补传/重复/会诊的确定性）
- patient / research：患者端视图与科研导出管控
"""

from .engine import AuditEngine, RequestRejected
from .events import EventLog
from .repository import Repository

__all__ = ["AuditEngine", "RequestRejected", "EventLog", "Repository"]
