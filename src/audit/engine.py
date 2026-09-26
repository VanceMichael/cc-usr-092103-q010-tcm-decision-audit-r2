"""审计引擎：请求受理、版本路由、处置留痕与决定重放。

确定性规则（与时间无关的部分全部可重放）：

- 重复请求：以 request_uid 为幂等键，永远返回首次结果；
- 灰度：患者化名稳定分桶，同一患者永远落同一桶；
- 离线补传：按实际就诊 occurred_at 路由当时获准/已发布的版本；
- 跨院会诊：禁用灰度，取各方共同批准版本交集，交集为空即驳回；
- 模型输出（model_output）与医生最终处置（CONFIRMED/REVISED/
  DISMISSED）分别建事件、分别存储，审计可清晰区分。
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone

from .consents import Consents, ConsentError, PURPOSE_CLINICAL
from .events import EventLog
from .inference import infer
from .minimize import validate_request, MinimizationError
from .organizations import Organizations
from .registry import Registry, MODEL_UID
from .roles import Roles
from .serde import digest, parse_dt
from .terminology import Terminology

# 同一版本严重不良结果达到阈值即自动停用（确定性停用条件之一）。
AE_STOP_THRESHOLD = 2


class RequestRejected(PermissionError):
    """请求被驳回；reason_code 稳定，可供审计统计。"""

    def __init__(self, reason_code: str, reason: str):
        super().__init__(f"[{reason_code}] {reason}")
        self.reason_code = reason_code
        self.reason = reason


@dataclass
class Decision:
    request: dict
    request_digest: str
    occurred_at: str
    org_chain: list[str]
    consent_snapshot: dict | None
    routing: dict | None
    model_output: dict | None
    status: str  # rejected / output_ready / confirmed / revised / dismissed
    rejection: dict | None = None
    disposition: dict | None = None
    duplicates: int = 0
    ae_reports: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "request": self.request,
            "request_digest": self.request_digest,
            "occurred_at": self.occurred_at,
            "org_chain": self.org_chain,
            "consent_snapshot": self.consent_snapshot,
            "routing": self.routing,
            "model_output": self.model_output,
            "status": self.status,
            "rejection": self.rejection,
            "disposition": self.disposition,
            "duplicates": self.duplicates,
            "ae_reports": self.ae_reports,
        }


class AuditEngine:
    def __init__(self, repo):
        self.repo = repo
        self.orgs = Organizations(repo)
        self.roles = Roles(repo)
        self.terminology = Terminology(repo)
        self.consents = Consents(repo)
        self.registry = Registry(repo, self.orgs)
        self.events = EventLog()
        self._decisions: dict[str, Decision] = {}
        self._idempotency: dict[str, str] = {}
        self._ae_serious_by_version: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 推理请求
    # ------------------------------------------------------------------
    def submit(self, req: dict) -> dict:
        """受理一次推理请求；重复请求返回首次结果（幂等）。"""
        uid = req.get("request_uid")
        idem = req.get("idempotency_key")
        existing = uid if uid and uid in self._idempotency else (
            idem if idem and idem in self._idempotency else None)
        if existing is not None:
            return self._on_duplicate(existing, req)

        try:
            validate_request(req)
        except MinimizationError as exc:
            return self._reject(req, "MINIMIZATION_VIOLATION", str(exc), actor=None)

        uid = req["request_uid"]
        if uid in self._decisions:
            return self._on_duplicate(uid, req)

        actor = {"role": req["clinic_role"], "pseudonym": req.get("clinician_pseudonym")}
        try:
            self.roles.require(req["clinic_role"], "PERM-CLINIC-INVOKE")
        except PermissionError as exc:
            return self._reject(req, "FORBIDDEN_ROLE", str(exc), actor=actor)

        occurred_at = parse_dt(req["occurred_at"])

        # 机构与当时获准病种
        try:
            org = self.orgs.get(req["org_id"])
            chain = self.orgs.chain(req["org_id"])
            reg = self.registry.registration_for(req["org_id"])
        except (KeyError, PermissionError) as exc:
            return self._reject(req, "ORG_NOT_AUTHORIZED", str(exc), actor=actor)
        if req["disease_code"] not in reg["disease_scope"]:
            return self._reject(
                req, "OUT_OF_DISEASE_SCOPE",
                f"机构 {req['org_id']} 获准病种为 {reg['disease_scope']}，"
                f"不含 {req['disease_code']}", actor=actor)

        # 患者授权（按发生时刻；离线补传同样按就诊时刻）
        parties = None
        if req.get("consultation_id"):
            parties = req.get("consultation_parties") or [req["org_id"]]
        try:
            if (req.get("patient_pseudonym")
                    and self.consents.patient_of(req["consent_id"])
                    != req["patient_pseudonym"]):
                raise ConsentError(
                    f"授权 {req['consent_id']} 不属于患者 {req['patient_pseudonym']}")
            snapshot = self.consents.check(
                req["consent_id"], purpose=PURPOSE_CLINICAL, org_id=req["org_id"],
                disease_code=req["disease_code"], at=occurred_at,
                consultation_parties=parties)
        except ConsentError as exc:
            return self._reject(req, "CONSENT_DENIED", str(exc), actor=actor)

        # 术语可解析
        unknown = self.terminology.check_request(
            req["disease_code"], req["features"]["syndrome_code"],
            req["features"]["manifestation_codes"])
        if unknown:
            return self._reject(req, "UNKNOWN_TERM_CODE",
                                f"术语编码无法解析或证病不符：{unknown}", actor=actor)

        # 版本路由（会诊/灰度/补传均在此得到确定结果）
        try:
            routing = self._route(req, reg, occurred_at, parties)
        except RequestRejected as exc:
            return self._reject(req, exc.reason_code, exc.reason, actor=actor)

        output = infer(req["features"], req["disease_code"], routing, self.terminology)

        decision = Decision(
            request=req, request_digest=digest(req), occurred_at=req["occurred_at"],
            org_chain=chain, consent_snapshot=snapshot, routing=routing,
            model_output=output, status="output_ready")
        self._decisions[uid] = decision
        self._idempotency[uid] = uid
        if req.get("idempotency_key"):
            self._idempotency[req["idempotency_key"]] = uid

        self.events.append(
            "REQUEST_ACCEPTED",
            {"request_uid": uid, "request_digest": decision.request_digest,
             "org_id": req["org_id"], "org_chain": chain,
             "offline": bool(req.get("offline")),
             "consultation_id": req.get("consultation_id"),
             "consent_snapshot": snapshot, "routing": routing},
            occurred_at=req["occurred_at"], actor=actor, org_id=req["org_id"])
        self.events.append(
            "MODEL_OUTPUT",
            {"request_uid": uid, "kind": "model_output",
             "model_version": output["model_version"],
             "output_digest": output["output_digest"],
             "recommended_formula_codes": output["recommended_formula_codes"],
             "confidence": output["confidence"], "supported": output["supported"]},
            occurred_at=req["occurred_at"], actor={"role": "SYSTEM", "pseudonym": "model-fz-assist"},
            org_id=req["org_id"])
        return {"request_uid": uid, "status": "output_ready",
                "duplicate": False, "decision": decision}

    def _route(self, req: dict, reg: dict, at: datetime,
               parties: list[str] | None) -> dict:
        if req.get("consultation_id"):
            return self._route_consultation(req, reg, at, parties)
        routed = self.registry.route_version(
            req["org_id"], patient_pseudonym=req.get("patient_pseudonym", ""),
            at=at, consultation=False)
        self._assert_effective(routed, at)
        return routed

    def _route_consultation(self, req: dict, reg: dict, at: datetime,
                            parties: list[str]) -> dict:
        # 会诊各方必须均在同一授权关系内且都已登记该模型。
        common: set[str] | None = None
        for party in parties:
            preg = self.registry.registration_for(party)
            if req["disease_code"] not in preg["disease_scope"]:
                raise RequestRejected(
                    "CONSULT_SCOPE_MISMATCH",
                    f"会诊方 {party} 未获准病种 {req['disease_code']}")
            versions = set(preg["allowed_versions"])
            common = versions if common is None else common & versions
        if not common:
            raise RequestRejected("CONSULT_NO_COMMON_VERSION",
                                  "会诊各方没有共同批准版本，禁止灰度版本会诊")
        # 确定性选择：主办机构默认版本若在交集中则用之，否则取最高语义版本。
        if reg["approved_version"] in common:
            chosen = reg["approved_version"]
        else:
            chosen = sorted(common, key=_semver, reverse=True)[0]
        info = self.registry.version_info(chosen)
        released = datetime.fromisoformat(info["released_on"] + "T00:00:00+08:00")
        routed = self.registry.route_version(
            req["org_id"], patient_pseudonym=req.get("patient_pseudonym", ""),
            at=at, consultation=True)
        # 以最终选定的共同版本为准重写路由要素与生效性检查。
        routed.update({
            "version": chosen,
            "routed_by": f"consultation:{req['consultation_id']}:common={sorted(common)}",
            "allowed_versions": sorted(common),
            "manifest_digest": info["manifest_digest"],
            "terminology_version": info["terminology_version"],
            "experience_source_codes": list(info["experience_source_codes"]),
            "status": info["status"],
            "released_on": info["released_on"],
            "released_before_request": released <= at,
            "stopped": self.registry.is_stopped(chosen),
        })
        self._assert_effective(routed, at)
        return routed

    @staticmethod
    def _assert_effective(routing: dict, at: datetime) -> None:
        if not routing["released_before_request"]:
            raise RequestRejected(
                "VERSION_NOT_RELEASED",
                f"版本 {routing['version']} 在请求时刻尚未发布，离线补传不得后借新版本")
        stop = routing.get("stopped")
        if stop and parse_dt(stop["at"]) <= at:
            raise RequestRejected("VERSION_STOPPED",
                                  f"版本 {routing['version']} 已于 {stop['at']} 停用：{stop['reason']}")

    def _on_duplicate(self, uid: str, req: dict) -> dict:
        original_uid = self._idempotency.get(uid, uid)
        decision = self._decisions[original_uid]
        decision.duplicates += 1
        self.events.append(
            "REQUEST_DEDUP",
            {"duplicate_request_uid": uid, "original_request_uid": original_uid,
             "original_output_digest":
                 decision.model_output["output_digest"] if decision.model_output else None,
             "client_payload_digest": digest(req) if req else None},
            occurred_at=req.get("occurred_at", decision.occurred_at),
            actor={"role": req.get("clinic_role", "UNKNOWN"),
                   "pseudonym": req.get("clinician_pseudonym")},
            org_id=req.get("org_id"))
        return {"request_uid": original_uid, "status": decision.status,
                "duplicate": True, "duplicate_of": original_uid, "decision": decision}

    def _reject(self, req: dict, code: str, reason: str, actor: dict | None) -> dict:
        uid = req.get("request_uid", f"unparseable-{digest(req)[:20]}")
        if uid in self._decisions:
            return self._on_duplicate(uid, req)
        rejection = {"reason_code": code, "reason": reason}
        decision = Decision(
            request=req, request_digest=digest(req),
            occurred_at=req.get("occurred_at", ""), org_chain=[],
            consent_snapshot=None, routing=None, model_output=None,
            status="rejected", rejection=rejection)
        self._decisions[uid] = decision
        self._idempotency[uid] = uid
        self.events.append(
            "REQUEST_REJECTED",
            {"request_uid": uid, "request_digest": decision.request_digest,
             "org_id": req.get("org_id"), **rejection},
            occurred_at=req.get("occurred_at", ""),
            actor=actor or {"role": req.get("clinic_role", "UNKNOWN")},
            org_id=req.get("org_id"))
        return {"request_uid": uid, "status": "rejected",
                "duplicate": False, "rejection": rejection, "decision": decision}

    # ------------------------------------------------------------------
    # 医生最终处置（与模型输出严格分离）
    # ------------------------------------------------------------------
    def _require_decision(self, uid: str) -> Decision:
        if uid not in self._decisions:
            raise KeyError(f"未知请求 {uid}")
        return self._decisions[uid]

    def confirm(self, uid: str, actor: dict, note: str = "") -> dict:
        self.roles.require(actor["role"], "PERM-CLINIC-CONFIRM")
        d = self._require_decision(uid)
        self._require_pending(d)
        if d.model_output is None:
            raise RequestRejected("NOTHING_TO_CONFIRM", "被驳回请求无模型建议可确认")
        d.status, d.disposition = "confirmed", {"action": "confirmed", "note": note}
        return self._disposition_event(uid, d, actor, "CLINICIAN_CONFIRMED", d.disposition)

    def revise(self, uid: str, actor: dict, final_formula_codes: list[str],
               note: str) -> dict:
        self.roles.require(actor["role"], "PERM-CLINIC-CONFIRM")
        d = self._require_decision(uid)
        self._require_pending(d)
        if d.model_output is None:
            raise RequestRejected("NOTHING_TO_REVISE", "被驳回请求无模型建议可修订")
        for code in final_formula_codes:
            if code not in self.terminology.formulas:
                raise MinimizationError(f"修订方剂编码不在术语表：{code}")
        d.status = "revised"
        d.disposition = {"action": "revised", "final_formula_codes": final_formula_codes,
                         "model_suggested_codes": d.model_output["recommended_formula_codes"],
                         "note": note}
        return self._disposition_event(uid, d, actor, "CLINICIAN_REVISED", d.disposition)

    def dismiss(self, uid: str, actor: dict, reason: str) -> dict:
        """医生驳回（不采纳）模型建议。"""
        self.roles.require(actor["role"], "PERM-CLINIC-CONFIRM")
        d = self._require_decision(uid)
        self._require_pending(d)
        d.status = "dismissed"
        d.disposition = {"action": "dismissed", "reason": reason}
        return self._disposition_event(uid, d, actor, "CLINICIAN_DISMISSED", d.disposition)

    @staticmethod
    def _require_pending(d: Decision) -> None:
        if d.disposition is not None:
            raise RequestRejected(
                "ALREADY_DISPOSITIONED",
                f"请求已作出医生处置（{d.disposition['action']}），处置不可反向修改")

    def _disposition_event(self, uid: str, d: Decision, actor: dict,
                           event_type: str, payload_extra: dict) -> dict:
        self.events.append(
            event_type,
            {"request_uid": uid,
             "model_output_digest": d.model_output["output_digest"] if d.model_output else None,
             **payload_extra},
            occurred_at=d.occurred_at, actor=actor, org_id=d.request.get("org_id"))
        return {"request_uid": uid, "status": d.status, "disposition": d.disposition}

    # ------------------------------------------------------------------
    # 不良结果上报与版本停用
    # ------------------------------------------------------------------
    def report_adverse_outcome(self, uid: str, actor: dict, severity: str,
                               description_code: str, reported_at: str | None = None) -> dict:
        self.roles.require(actor["role"], "PERM-AE-REPORT")
        d = self._require_decision(uid)
        if not d.model_output:
            raise RequestRejected("AE_ON_REJECTED_REQUEST", "被驳回请求不关联模型建议")
        report = {"severity": severity, "description_code": description_code}
        d.ae_reports.append(report)
        # 不良结果在“上报时刻”入链；停用自上报时刻生效，不回写历史就诊时刻。
        event_time = reported_at or datetime.now(timezone.utc).isoformat()
        self.events.append(
            "ADVERSE_OUTCOME",
            {"request_uid": uid, "model_version": d.model_output["model_version"],
             **report},
            occurred_at=event_time, actor=actor, org_id=d.request.get("org_id"))

        if severity == "serious":
            version = d.model_output["model_version"]
            count = self._ae_serious_by_version.get(version, 0) + 1
            self._ae_serious_by_version[version] = count
            if count >= AE_STOP_THRESHOLD:
                reason = f"严重不良结果达 {count} 例（阈值 {AE_STOP_THRESHOLD}），自动停用"
                event = self.events.append(
                    "VERSION_STOPPED",
                    {"model_uid": MODEL_UID, "version": version, "trigger": "adverse_threshold",
                     "serious_count": count, "reason": reason},
                    occurred_at=event_time, actor={"role": "SYSTEM", "pseudonym": "audit-engine"},
                    org_id=d.request.get("org_id"))
                self.registry.stop(MODEL_UID, version, event.event_id, reason,
                                   parse_dt(event_time))
        return {"request_uid": uid, "adverse_recorded": True}

    def stop_version(self, version: str, actor: dict, reason: str, at: str) -> dict:
        """医务处手工停用。"""
        self.roles.require(actor["role"], "PERM-REG-APPROVE")
        event = self.events.append(
            "VERSION_STOPPED",
            {"model_uid": MODEL_UID, "version": version, "trigger": "manual", "reason": reason},
            occurred_at=at, actor=actor, org_id=None)
        self.registry.stop(MODEL_UID, version, event.event_id, reason, parse_dt(at))
        return {"version": version, "stopped": True, "event_id": event.event_id}

    # ------------------------------------------------------------------
    # 授权撤回
    # ------------------------------------------------------------------
    def withdraw_consent(self, consent_id: str, actor: dict, at: str, reason: str) -> dict:
        self.roles.require(actor["role"], "PERM-CONSENT-WITHDRAW")
        event = self.events.append(
            "CONSENT_WITHDRAWN",
            {"consent_id": consent_id, "reason": reason},
            occurred_at=at, actor=actor, org_id=None)
        self.consents.withdraw(consent_id, event.event_id)
        return {"consent_id": consent_id, "withdrawn": True, "event_id": event.event_id}

    # ------------------------------------------------------------------
    # 审计重放
    # ------------------------------------------------------------------
    def get_decision(self, uid: str) -> Decision:
        return self._require_decision(uid)

    def decisions_for_patient(self, patient_pseudonym: str) -> list[Decision]:
        return [d for d in self._decisions.values()
                if d.request.get("patient_pseudonym") == patient_pseudonym]

    def replay(self, uid: str) -> dict:
        """按请求时刻重放临床决定，并比对存储摘要验证未被改写。"""
        d = self._require_decision(uid)
        if d.status == "rejected":
            return {"request_uid": uid, "replay_status": "rejected",
                    "rejection": d.rejection,
                    "stored_request_digest": d.request_digest,
                    "request_now_digest": digest(d.request),
                    "chain_verified": self._chain_ok()}
        routing = self.registry.route_version(
            d.request["org_id"], patient_pseudonym=d.request.get("patient_pseudonym", ""),
            at=parse_dt(d.occurred_at),
            consultation=bool(d.routing["routed_by"].startswith("consultation:")))
        if d.routing["routed_by"].startswith("consultation:"):
            # 会诊路由结果本身已固化在事件中，重放取共同版本交集再核对。
            routing["version"] = d.routing["version"]
            routing["allowed_versions"] = d.routing["allowed_versions"]
            routing["routed_by"] = d.routing["routed_by"]
            routing["manifest_digest"] = d.routing["manifest_digest"]
            routing["experience_source_codes"] = d.routing["experience_source_codes"]
        replay_output = infer(d.request["features"], d.request["disease_code"],
                              routing, self.terminology)
        return {
            "request_uid": uid,
            "replay_status": "matches"
            if replay_output["output_digest"] == d.model_output["output_digest"]
            else "MISMATCH",
            "stored_request_digest": d.request_digest,
            "request_now_digest": digest(d.request),
            "org_chain": d.org_chain,
            "consent_snapshot": d.consent_snapshot,
            "routing": d.routing,
            "model_output": d.model_output,
            "replay_output_digest": replay_output["output_digest"],
            "doctor_disposition": d.disposition,
            "decision_status": d.status,
            "adverse_outcomes": d.ae_reports,
            "model_vs_doctor": _model_vs_doctor(d),
            "chain_verified": self._chain_ok(),
        }

    def compare_versions(self, uid: str, versions: list[str] | None = None) -> dict:
        """同一病例、同一实际输入在多个模型版本下的差异解释。"""
        d = self._require_decision(uid)
        if d.model_output is None:
            raise RequestRejected("REPLAY_REJECTED_REQUEST", "被驳回请求无模型输出可比较")
        versions = versions or ["1.4.0", "1.5.0"]
        results = {}
        for version in versions:
            info = self.registry.version_info(version)
            routed = {"version": version, "manifest_digest": info["manifest_digest"],
                      "terminology_version": info["terminology_version"],
                      "experience_source_codes": list(info["experience_source_codes"]),
                      "stopped": self.registry.is_stopped(version),
                      "released_before_request": True, "routed_by": "audit-comparison",
                      "allowed_versions": versions, "released_on": info["released_on"],
                      "status": info["status"]}
            out = infer(d.request["features"], d.request["disease_code"],
                        routed, self.terminology)
            results[version] = out

        base, *others = versions
        diffs = {}
        for v in others:
            a, b = results[base], results[v]
            diffs[v] = {
                "recommended_formula_codes": {
                    "base": a["recommended_formula_codes"], "other": b["recommended_formula_codes"]},
                "confidence": {"base": a["confidence"], "other": b["confidence"]},
                "rule_change": {
                    "base_rule_set": a["rule_trace"]["rule_set"],
                    "other_rule_set": b["rule_trace"]["rule_set"],
                    "base_threshold": a["rule_trace"]["score_threshold"],
                    "other_threshold": b["rule_trace"]["score_threshold"],
                    "base_policy": a["rule_trace"]["primary_policy"],
                    "other_policy": b["rule_trace"]["primary_policy"],
                },
                "experience_sources": {
                    "base": a["rule_trace"]["experience_source_codes"],
                    "other": b["rule_trace"]["experience_source_codes"]},
            }
        return {"request_uid": uid, "actual_input_digest": d.request_digest,
                "base_version": base, "outputs": results, "differences": diffs,
                "doctor_disposition": d.disposition}

    def _chain_ok(self) -> bool:
        try:
            self.events.verify()
            return True
        except Exception:
            return False


def _model_vs_doctor(d: Decision) -> dict:
    if not d.model_output:
        return {"model": None, "doctor": d.disposition}
    model_codes = d.model_output["recommended_formula_codes"]
    final_codes = None
    if d.disposition:
        final_codes = d.disposition.get("final_formula_codes", model_codes)
    return {"model_recommended": model_codes,
            "doctor_final": final_codes,
            "doctor_action": d.disposition["action"] if d.disposition else "pending",
            "identical": (d.disposition is not None
                          and d.disposition["action"] == "confirmed")}


def _semver(version: str) -> tuple:
    head = version.split("-")[0]
    return tuple(int(x) for x in head.split("."))
