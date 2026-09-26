"""患者授权：用途、病种、机构范围、有效期与撤回。

授权记录在请求时刻被拍成不可变快照（摘要进入审计记录）；
撤回通过事件追加生效，从不修改或删除原授权文本。
"""

from datetime import datetime

from .serde import digest
from .repository import Repository

PURPOSE_CLINICAL = "clinical_decision_support"
PURPOSE_RESEARCH = "research"
PURPOSE_TRAINING = "talent_training"


class ConsentError(PermissionError):
    """授权校验失败。"""


class Consents:
    def __init__(self, repo: Repository):
        self._grants: dict[str, dict] = {}
        self._patient_of: dict[str, str] = {}
        for patient in repo.consents_doc["patients"]:
            for grant in patient["consents"]:
                self._grants[grant["consent_id"]] = grant
                self._patient_of[grant["consent_id"]] = patient["pseudonym"]
        # 资料中已标注撤回的授权（其撤回事件号同时登记）。
        self._withdrawn: dict[str, str] = {
            cid: g["withdrawn_event_id"]
            for cid, g in self._grants.items()
            if g["status"] == "withdrawn"
        }

    def patient_of(self, consent_id: str) -> str:
        return self._patient_of[consent_id]

    def grant(self, consent_id: str) -> dict:
        if consent_id not in self._grants:
            raise ConsentError(f"未知授权 {consent_id}")
        return self._grants[consent_id]

    def is_withdrawn(self, consent_id: str) -> bool:
        return consent_id in self._withdrawn

    def withdraw(self, consent_id: str, event_id: str) -> None:
        """登记撤回；重复撤回保持幂等（指向同一首个撤回事件）。"""
        if consent_id not in self._grants:
            raise ConsentError(f"未知授权 {consent_id}")
        self._withdrawn.setdefault(consent_id, event_id)

    def snapshot(self, consent_id: str) -> dict:
        """请求时刻的授权快照（仅留存判断所需字段）。"""
        g = self.grant(consent_id)
        return {
            "consent_id": g["consent_id"],
            "purposes": g["purposes"],
            "disease_scope": g["disease_scope"],
            "org_ids": g["org_ids"],
            "cross_org_consult": g["cross_org_consult"],
            "granted_on": g["granted_on"],
            "valid_until": g["valid_until"],
            "withdrawn": self.is_withdrawn(consent_id),
            "snapshot_digest": digest({k: v for k, v in g.items()
                                       if k not in ("status", "withdrawn_event_id")}),
        }

    def check(self, consent_id: str, *, purpose: str, org_id: str,
              disease_code: str, at: datetime,
              consultation_parties: list[str] | None = None) -> dict:
        """按当时状态校验授权，失败抛 ConsentError；成功返回快照。"""
        g = self.grant(consent_id)
        if self.is_withdrawn(consent_id):
            raise ConsentError(f"授权 {consent_id} 已撤回（{self._withdrawn[consent_id]}）")
        if purpose not in g["purposes"]:
            raise ConsentError(f"授权 {consent_id} 不含用途 {purpose}")
        if disease_code not in g["disease_scope"]:
            raise ConsentError(f"授权 {consent_id} 不覆盖病种 {disease_code}")
        granted_on = datetime.fromisoformat(g["granted_on"])
        valid_until = datetime.fromisoformat(g["valid_until"])
        if not (granted_on <= at <= valid_until):
            raise ConsentError(f"授权 {consent_id} 在 {at.isoformat()} 不在有效期内")
        covered = set(g["org_ids"])
        if consultation_parties:
            if not g["cross_org_consult"]:
                raise ConsentError(f"授权 {consent_id} 不允许跨院会诊")
            covered.update(consultation_parties)
        if org_id not in covered:
            raise ConsentError(f"授权 {consent_id} 不覆盖机构 {org_id}")
        return self.snapshot(consent_id)
