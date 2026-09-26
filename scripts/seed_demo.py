#!/usr/bin/env python3
"""端到端演示与可执行调用样例。

运行：python3 scripts/seed_demo.py
产物写入 outputs/（已在 .gitignore 中）：
- event_log.jsonl      只增哈希链事件
- decisions.json       全部临床决定审计记录
- replay.json          REQ-0001 的重放结果与跨版本差异解释
- patient_view.json    患者端可见内容
- research_export.json 科研导出结果
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.audit import AuditEngine, Repository
from src.audit.patient import PatientPortal
from src.audit.research import ResearchExport

OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)


def main() -> None:
    repo = Repository()
    engine = AuditEngine(repo)

    samples = json.loads((ROOT / "fixtures" / "invocation_samples.json").read_text("utf-8"))
    # 样例文件中的 expect_rejection* / duplicate_of / note 为说明性注释，不属请求契约。
    annotation_keys = {"expect_rejection", "expect_rejection_reason",
                       "duplicate_of", "captured_at_offline_note", "note"}
    requests = {}
    for r in samples["requests"]:
        if "duplicate_of" in r:
            continue
        requests[r["request_uid"]] = {k: v for k, v in r.items()
                                      if k not in annotation_keys}

    def submit(uid: str):
        return engine.submit(requests[uid])

    # 1) 徐汇院区 1.5.0 常规请求（同患者后来在会诊中用到 1.4.0）
    r1 = submit("REQ-20260915-0001")
    assert r1["status"] == "output_ready"
    assert r1["decision"].routing["version"] == "1.5.0"

    # 2) 浦东院区越病种（肺癌未获准）→ 必须驳回
    r2 = submit("REQ-20260915-0002")
    assert r2["status"] == "rejected"
    assert r2["rejection"]["reason_code"] == "OUT_OF_DISEASE_SCOPE"

    # 3) 区域站点离线补传：按实际就诊时刻路由到当时的 1.4.0
    r3 = submit("REQ-20260916-0003")
    assert r3["decision"].routing["version"] == "1.4.0"

    # 4) 跨院会诊（徐汇—大华）：共同批准版本交集仅 1.4.0，禁用灰度
    r4 = submit("REQ-20260918-0004")
    assert r4["decision"].routing["version"] == "1.4.0"
    assert "consultation" in r4["decision"].routing["routed_by"]

    # 5) 重复请求（网络重试，同一 request_uid）→ 回首次结果并记 REQUEST_DEDUP
    dup = engine.submit(requests["REQ-20260915-0001"])
    assert dup["duplicate"] is True and dup["decision"].duplicates == 1

    # 6) 灰度患者 P-000005 落 0 号桶（<10）→ 1.6.0-rc1；主治评估后不采纳
    canary_req = {
        "request_uid": "REQ-20260912-0009",
        "occurred_at": "2026-09-12T10:00:00+08:00",
        "org_id": "ORG-LH-XJH",
        "clinic_role": "ROLE-ATTENDING",
        "clinician_pseudonym": "D-AA01",
        "patient_pseudonym": "P-000005",
        "consent_id": "CON-2026-0005",
        "disease_code": "DIS-COPD",
        "features": {"syndrome_code": "SYN-FHQF",
                     "manifestation_codes": ["M-SF", "M-OB"],
                     "tongue_code": "T-DH", "pulse_code": "P-XR",
                     "age_band": "40-59", "sex": "M"},
        "offline": False, "consultation_id": None,
    }
    rc = engine.submit(canary_req)
    assert rc["decision"].routing["version"] == "1.6.0-rc1", rc["decision"].routing
    engine.dismiss(canary_req["request_uid"],
                   {"role": "ROLE-ATTENDING", "pseudonym": "D-AA01"},
                   reason="候选版建议依据不足，暂不采纳")

    # 7) 同一患者的两次早期就诊（1.4.0），用于不良结果阈值演示与科研纵向行
    earlier = dict(canary_req)
    earlier.update({
        "request_uid": "REQ-20260914-0010",
        "occurred_at": "2026-09-14T09:00:00+08:00",
        "org_id": "ORG-PDTCM-CY",
        "clinician_pseudonym": "D-CC13",
        "patient_pseudonym": "P-2D88E0",
        "consent_id": "CON-2026-0004",
        "offline": True,
    })
    earlier["features"] = dict(canary_req["features"])
    earlier["features"].update({"age_band": ">=75", "sex": "M"})
    r_early = engine.submit(earlier)
    assert r_early["decision"].routing["version"] == "1.4.0"

    # P-7F3A21 在 CON-2026-0002（含科研用途）下的另两次复诊
    followups = []
    for day in ("05", "19"):
        req = dict(requests["REQ-20260915-0001"])
        req["request_uid"] = f"REQ-202609{day}-0020"
        req["occurred_at"] = f"2026-09-{day}T11:00:00+08:00"
        followups.append(engine.submit(req)["request_uid"])

    # 8) 主治处置：确认/修订（住院医无权确认在角色层被拒绝）
    attending = {"role": "ROLE-ATTENDING", "pseudonym": "D-AA01"}
    engine.confirm("REQ-20260915-0001", attending, note="遵模型建议，玉屏风散固表")
    engine.confirm("REQ-20260916-0003", {"role": "ROLE-ATTENDING", "pseudonym": "D-CC13"},
                   note="站点按1.4方案执行")
    engine.confirm("REQ-20260918-0004", attending, note="会诊按共同版本执行")
    engine.confirm("REQ-20260914-0010", {"role": "ROLE-ATTENDING", "pseudonym": "D-CC13"},
                   note="补传后主治补确认")
    for uid in followups:
        engine.confirm(uid, attending, note="复诊守方")

    # 9) 不良结果：同一 1.4.0 两例严重（09-19 上报）→ 自动停用事件
    engine.report_adverse_outcome(
        "REQ-20260914-0010", {"role": "ROLE-ATTENDING", "pseudonym": "D-CC13"},
        severity="serious", description_code="AE-LIVER-FUNCTION",
        reported_at="2026-09-19T08:30:00+08:00")
    engine.report_adverse_outcome(
        "REQ-20260916-0003", {"role": "ROLE-ATTENDING", "pseudonym": "D-CC13"},
        severity="serious", description_code="AE-LIVER-FUNCTION",
        reported_at="2026-09-19T09:10:00+08:00")

    # 10) 停用后新请求（含区域站点与医联体成员联动）→ 确定性驳回
    after_stop = dict(earlier)
    after_stop.update({"request_uid": "REQ-20260920-0011",
                       "occurred_at": "2026-09-20T09:00:00+08:00", "offline": False})
    r_after = engine.submit(after_stop)
    assert r_after["status"] == "rejected"
    assert r_after["rejection"]["reason_code"] == "VERSION_STOPPED"

    # 11) 授权撤回（运行期）：CON-2026-0001 撤回后新请求被拒
    engine.withdraw_consent(
        "CON-2026-0001", {"role": "ROLE-CONSULT-DESK", "pseudonym": "DESK-01"},
        at="2026-09-21T09:00:00+08:00", reason="患者书面撤回辅助决策与科研附加授权")
    withdrawn_req = dict(requests["REQ-20260918-0004"])
    withdrawn_req["request_uid"] = "REQ-20260921-0012"
    withdrawn_req["occurred_at"] = "2026-09-21T10:00:00+08:00"
    r_withdrawn = engine.submit(withdrawn_req)
    assert r_withdrawn["status"] == "rejected"
    assert r_withdrawn["rejection"]["reason_code"] == "CONSENT_DENIED"

    # 12) 审计问答：重放 + “同一病例为何两版本建议不同”
    replay = engine.replay("REQ-20260915-0001")
    comparison = engine.compare_versions("REQ-20260915-0001", ["1.4.0", "1.5.0"])

    # 13) 患者端：只见医生确认内容，不见模型原案/驳回/灰度未采纳建议
    portal = PatientPortal(engine)
    patient_view = portal.view("P-7F3A21", {"role": "ROLE-PATIENT", "pseudonym": "P-7F3A21"})

    # 14) 科研导出：用途+伦理+数据集范围、独立盐值化名、k-匿名
    research = ResearchExport(engine)
    export = research.export_dataset(
        dataset="DS-COPD-OUTCOME", ethics_ref="伦理2026-052",
        actor={"role": "ROLE-RESEARCHER", "pseudonym": "DR-R-02"}, k=3)
    research.deny_export(
        dataset="DS-LC-UNAPPROVED", ethics_ref="伦理2019-000",
        actor={"role": "ROLE-RESEARCHER", "pseudonym": "DR-R-02"},
        reason="数据集与伦理批件均未在授权 research_scope 内",
        at="2026-09-22T09:00:00+08:00")

    # 15) 哈希链自检
    engine.events.verify()

    (OUT / "event_log.jsonl").write_text(engine.events.export_jsonl(), encoding="utf-8")
    (OUT / "decisions.json").write_text(
        json.dumps({uid: d.to_dict() for uid, d in engine._decisions.items()},  # noqa: SLF001
                   ensure_ascii=False, indent=2, default=_default), encoding="utf-8")
    (OUT / "replay.json").write_text(
        json.dumps({"replay": replay, "version_comparison": comparison},
                   ensure_ascii=False, indent=2, default=_default), encoding="utf-8")
    (OUT / "patient_view.json").write_text(
        json.dumps(patient_view, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "research_export.json").write_text(
        json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"事件数: {len(engine.events)}（哈希链校验通过）")
    print(f"1.4.0 -> {comparison['outputs']['1.4.0']['recommended_formula_codes']}")
    print(f"1.5.0 -> {comparison['outputs']['1.5.0']['recommended_formula_codes']}")
    print(f"患者端条目: {len(patient_view)}；科研导出行: {export['included']}")


def _default(obj):
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    return str(obj)


if __name__ == "__main__":
    main()
