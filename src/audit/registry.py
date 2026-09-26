"""模型版本登记：版本、适用范围、灰度与停用条件。

停用同样以事件追加（stop），登记本身不被改写；历史版本必须
保留可执行（确定性）以便审计重放。
"""

from datetime import datetime

from .organizations import Organizations
from .repository import Repository
from .serde import stable_bucket

MODEL_UID = "model-fz-assist"
CANARY_SALT = "canary-bucket-2026"


class Registry:
    def __init__(self, repo: Repository, orgs: Organizations):
        self.models = repo.models
        self.registrations = repo.registrations
        self.orgs = orgs
        # 运行期停用：model_uid|version -> 停用事件信息（只增）
        self._stops: dict[str, dict] = {}

    def model(self, model_uid: str = MODEL_UID) -> dict:
        if model_uid not in self.models:
            raise KeyError(f"未登记模型 {model_uid}")
        return self.models[model_uid]

    def version_info(self, version: str, model_uid: str = MODEL_UID) -> dict:
        for v in self.model(model_uid)["versions"]:
            if v["version"] == version:
                return v
        raise KeyError(f"未登记版本 {model_uid}@{version}")

    def registration_for(self, org_id: str, model_uid: str = MODEL_UID) -> dict:
        reg = self.orgs.resolve_registration(org_id, model_uid, self.registrations)
        if reg is None:
            raise PermissionError(f"机构 {org_id} 未登记 {model_uid} 的使用授权")
        if not reg["allowed_versions"]:
            raise PermissionError(f"机构 {org_id} 无权调用 {model_uid}（治理机构不直接调用）")
        return reg

    def stop(self, model_uid: str, version: str, event_id: str, reason: str,
             at: datetime) -> None:
        """停用某版本（全局）；重复停用幂等，保留首个停用事件。"""
        self.version_info(version, model_uid)
        self._stops.setdefault(f"{model_uid}|{version}",
                               {"event_id": event_id, "reason": reason, "at": at.isoformat()})

    def is_stopped(self, version: str, model_uid: str = MODEL_UID) -> dict | None:
        return self._stops.get(f"{model_uid}|{version}")

    def route_version(self, org_id: str, *, patient_pseudonym: str,
                      at: datetime, consultation: bool = False,
                      model_uid: str = MODEL_UID) -> dict:
        """确定某请求在机构、时刻、灰度下的版本。

        - 会诊：禁用灰度，取共同批准版本（由 engine 求交集后传入注册集合）
        - 灰度：按患者化名稳定分桶，桶号 < percent 才进入新版本；
          同患者永远落同桶，灰度切换可解释、可重放。
        """
        reg = self.registration_for(org_id, model_uid)
        allowed = list(reg["allowed_versions"])
        canary = reg.get("canary")

        chosen = reg["approved_version"]
        routed_by = "approved"

        if canary and canary.get("enabled") and not consultation:
            new_version = canary["new_version"]
            info = self.version_info(new_version, model_uid)
            released = datetime.fromisoformat(info["released_on"] + "T00:00:00+08:00")
            # 灰度候选必须在请求时刻已发布；未发布时回落到默认版本，
            # 离线补传不能因“后来发布的候选”而改变历史路由。
            if released <= at:
                bucket = stable_bucket(f"{patient_pseudonym}|{new_version}", CANARY_SALT)
                if bucket < canary["percent"]:
                    chosen = new_version
                    routed_by = f"canary:bucket={bucket}<{canary['percent']}"
                else:
                    routed_by = f"stable:bucket={bucket}>={canary['percent']}"
            else:
                routed_by = f"stable:canary-not-released-until={info['released_on']}"

        info = self.version_info(chosen, model_uid)
        stop = self.is_stopped(chosen, model_uid)
        released = datetime.fromisoformat(info["released_on"] + "T00:00:00+08:00")
        return {
            "version": chosen,
            "allowed_versions": allowed,
            "manifest_digest": info["manifest_digest"],
            "terminology_version": info["terminology_version"],
            "experience_source_codes": list(info["experience_source_codes"]),
            "released_on": info["released_on"],
            "status": info["status"],
            "routed_by": routed_by,
            "stopped": stop,
            "released_before_request": released <= at,
        }
