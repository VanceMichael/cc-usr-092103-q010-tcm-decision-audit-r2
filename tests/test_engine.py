import unittest

from src.audit import AuditEngine, Repository
from src.audit.minimize import MinimizationError
from tests.test_domain import base_request

ATTENDING = {"role": "ROLE-ATTENDING", "pseudonym": "D-AA01"}
RESIDENT = {"role": "ROLE-RESIDENT", "pseudonym": "D-BB07"}


def engine_with(req: dict) -> tuple[AuditEngine, dict]:
    engine = AuditEngine(Repository())
    result = engine.submit(req)
    return engine, result


class RoutingTest(unittest.TestCase):
    def test_xuhui_default_is_150(self):
        engine, result = engine_with(base_request())
        self.assertEqual(result["status"], "output_ready")
        self.assertEqual(result["decision"].routing["version"], "1.5.0")

    def test_meilong_stays_on_140_without_upgrade_approval(self):
        # 梅陇仅批准 1.4.0；CON-2026-0001 覆盖梅陇院区
        _, result = engine_with(base_request(org_id="ORG-LH-MH",
                                             consent_id="CON-2026-0001"))
        self.assertEqual(result["decision"].routing["version"], "1.4.0")

    def test_regional_site_offline_replays_at_occurred_time(self):
        req = base_request(
            request_uid="REQ-OFFLINE-1", org_id="ORG-PDTCM-CY",
            patient_pseudonym="P-2D88E0", consent_id="CON-2026-0004",
            clinician_pseudonym="D-CC13", occurred_at="2026-09-16T14:20:00+08:00",
            offline=True, features={
                "syndrome_code": "SYN-FHQF",
                "manifestation_codes": ["M-SF", "M-OB", "M-LB"],
                "tongue_code": "T-DH", "pulse_code": "P-XR",
                "age_band": ">=75", "sex": "M"})
        _, result = engine_with(req)
        self.assertEqual(result["decision"].routing["version"], "1.4.0")
        self.assertTrue(result["decision"].request["offline"])

    def test_offline_cannot_borrow_unreleased_version(self):
        # 就诊发生在 1.6.0-rc1 发布之前，即便后来补传也不得使用
        req = base_request(request_uid="REQ-OLD-1",
                           occurred_at="2026-09-05T09:00:00+08:00",
                           patient_pseudonym="P-000005", consent_id="CON-2026-0005")
        _, result = engine_with(req)
        # 09-05 时 rc1（09-10 发布）尚不存在，患者虽在灰度桶也只能走 1.5.0
        self.assertEqual(result["decision"].routing["version"], "1.5.0")


class CanaryTest(unittest.TestCase):
    def _canary_req(self, patient, consent):
        return base_request(
            request_uid=f"REQ-CAN-{patient}", org_id="ORG-LH-XJH",
            patient_pseudonym=patient, consent_id=consent,
            disease_code="DIS-COPD",
            features={"syndrome_code": "SYN-FHQF",
                      "manifestation_codes": ["M-SF", "M-OB"],
                      "tongue_code": "T-DH", "pulse_code": "P-XR",
                      "age_band": "40-59", "sex": "M"})

    def test_patient_in_canary_bucket_gets_rc(self):
        _, result = engine_with(self._canary_req("P-000005", "CON-2026-0005"))
        self.assertEqual(result["decision"].routing["version"], "1.6.0-rc1")
        self.assertIn("canary", result["decision"].routing["routed_by"])

    def test_patient_outside_bucket_gets_approved(self):
        _, result = engine_with(self._canary_req("P-7F3A21", "CON-2026-0002"))
        self.assertEqual(result["decision"].routing["version"], "1.5.0")
        self.assertIn("stable", result["decision"].routing["routed_by"])

    def test_bucket_is_stable_across_submissions(self):
        engine = AuditEngine(Repository())
        versions = set()
        for i in range(3):
            req = self._canary_req("P-000005", "CON-2026-0005")
            req["request_uid"] = f"REQ-CAN-REP-{i}"
            versions.add(engine.submit(req)["decision"].routing["version"])
        self.assertEqual(versions, {"1.6.0-rc1"})


class RejectionTest(unittest.TestCase):
    def test_disease_out_of_scope_rejected(self):
        req = base_request(org_id="ORG-LH-PD", disease_code="DIS-LC",
                           consent_id="CON-2026-0001")
        req["features"] = {"syndrome_code": "SYN-FYXR",
                           "manifestation_codes": ["M-DK", "M-WXR", "M-XS"],
                           "tongue_code": "T-HJ", "pulse_code": "P-XS",
                           "age_band": "60-74", "sex": "F"}
        _, result = engine_with(req)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["rejection"]["reason_code"], "OUT_OF_DISEASE_SCOPE")

    def test_resident_forbidden_to_invoke_via_role(self):
        # 住院医有 INVOKE 权限；无权限角色才拒绝
        engine = AuditEngine(Repository())
        req = base_request(clinic_role="ROLE-AUDITOR")
        result = engine.submit(req)
        self.assertEqual(result["rejection"]["reason_code"], "FORBIDDEN_ROLE")

    def test_consent_withdrawn_blocks_request(self):
        engine = AuditEngine(Repository())
        # CON-2026-0003 在资料中已是 withdrawn
        req = base_request(consent_id="CON-2026-0003", patient_pseudonym="P-9C10B4",
                           disease_code="DIS-LC")
        req["features"] = {"syndrome_code": "SYN-FYXR",
                           "manifestation_codes": ["M-DK", "M-WXR"],
                           "tongue_code": "T-HJ", "pulse_code": "P-XS",
                           "age_band": "60-74", "sex": "F"}
        result = engine.submit(req)
        self.assertEqual(result["rejection"]["reason_code"], "CONSENT_DENIED")
        self.assertIn("已撤回", result["rejection"]["reason"])

    def test_consent_wrong_org_blocked(self):
        # CON-2026-0004 仅覆盖曹园站点/浦东中心
        req = base_request(org_id="ORG-LH-XJH", patient_pseudonym="P-2D88E0",
                           consent_id="CON-2026-0004")
        _, result = engine_with(req)
        self.assertEqual(result["rejection"]["reason_code"], "CONSENT_DENIED")

    def test_minimization_violation_rejected(self):
        engine = AuditEngine(Repository())
        req = base_request()
        req["身份证号"] = "310101199003078888"
        result = engine.submit(req)
        self.assertEqual(result["rejection"]["reason_code"], "MINIMIZATION_VIOLATION")


class IdempotencyTest(unittest.TestCase):
    def test_duplicate_returns_first_result(self):
        engine = AuditEngine(Repository())
        first = engine.submit(base_request())
        second = engine.submit(base_request())
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["decision"], first["decision"])
        self.assertEqual(first["decision"].duplicates, 1)
        # 只受理一次：没有第二条 MODEL_OUTPUT
        self.assertEqual(len(engine.events.of_type("MODEL_OUTPUT")), 1)
        self.assertEqual(len(engine.events.of_type("REQUEST_DEDUP")), 1)

    def test_idempotency_key_alias_dedups(self):
        engine = AuditEngine(Repository())
        req = base_request(idempotency_key="CLIENT-KEY-77")
        engine.submit(req)
        again = dict(req)
        again["request_uid"] = "REQ-DIFFERENT-UID"
        result = engine.submit(again)
        self.assertTrue(result["duplicate"])
        self.assertEqual(result["duplicate_of"], "REQ-TEST-0001")


class ConsultationTest(unittest.TestCase):
    def _consult(self, parties, consent="CON-2026-0001"):
        return base_request(
            request_uid="REQ-CONSULT-1",
            consultation_id="CON-LH-XHCH-20260918-01",
            consultation_parties=parties, consent_id=consent)

    def test_common_version_intersection(self):
        _, result = engine_with(self._consult(["ORG-LH-XJH", "ORG-XHCH"]))
        routing = result["decision"].routing
        self.assertEqual(routing["version"], "1.4.0")
        self.assertNotIn("1.6.0-rc1", routing["allowed_versions"])

    def test_consultation_disallows_canary(self):
        # 即便患者在灰度桶，会诊也不得使用 rc 候选
        req = self._consult(["ORG-LH-XJH", "ORG-XHCH"])
        req["patient_pseudonym"] = "P-000005"
        req["consent_id"] = "CON-2026-0005"
        # 该授权不允许会诊 → 先被授权拦截
        _, result = engine_with(req)
        self.assertEqual(result["rejection"]["reason_code"], "CONSENT_DENIED")

    def test_consultation_no_common_version(self):
        # 梅陇仅 1.4.0；航头仅 1.5.0 → 交集为空。
        # CON-2026-0001 覆盖全部龙华院区且允许跨院会诊。
        req = base_request(
            request_uid="REQ-CONSULT-2", org_id="ORG-LH-MH",
            consent_id="CON-2026-0001",
            consultation_id="CON-NO-COMMON",
            consultation_parties=["ORG-LH-MH", "ORG-LH-HK"])
        _, result = engine_with(req)
        self.assertEqual(result["rejection"]["reason_code"], "CONSULT_NO_COMMON_VERSION")


class DispositionTest(unittest.TestCase):
    def test_resident_cannot_confirm(self):
        engine = AuditEngine(Repository())
        engine.submit(base_request())
        with self.assertRaises(PermissionError):
            engine.confirm("REQ-TEST-0001", RESIDENT)

    def test_revise_separates_model_and_doctor(self):
        engine = AuditEngine(Repository())
        engine.submit(base_request())
        engine.revise("REQ-TEST-0001", ATTENDING,
                      final_formula_codes=["FM-SJZT"], note="改为单纯健脾")
        d = engine.get_decision("REQ-TEST-0001")
        self.assertEqual(d.status, "revised")
        self.assertNotEqual(d.model_output["recommended_formula_codes"],
                            d.disposition["final_formula_codes"])

    def test_dismiss_and_cannot_confirm_rejected(self):
        engine = AuditEngine(Repository())
        engine.submit(base_request())
        engine.dismiss("REQ-TEST-0001", ATTENDING, reason="不采纳")
        with self.assertRaises(Exception):
            engine.confirm("REQ-TEST-0001", ATTENDING)


class AdverseAndStopTest(unittest.TestCase):
    def _two_14_requests(self, engine):
        specs = [
            ("REQ-AE-1", "ORG-PDTCM-CY", "P-2D88E0", "CON-2026-0004", "D-CC13", ">=75", "M"),
            ("REQ-AE-2", "ORG-LH-MH", "P-7F3A21", "CON-2026-0001", "D-AA01", "60-74", "F"),
        ]
        for uid, org, patient, consent, doc, age, sex in specs:
            req = base_request(
                request_uid=uid, org_id=org, patient_pseudonym=patient,
                consent_id=consent, clinician_pseudonym=doc,
                occurred_at="2026-09-10T09:00:00+08:00",
                features={"syndrome_code": "SYN-FHQF",
                          "manifestation_codes": ["M-SF", "M-OB"],
                          "tongue_code": "T-DH", "pulse_code": "P-XR",
                          "age_band": age, "sex": sex})
            engine.submit(req)
            engine.confirm(uid, {"role": "ROLE-ATTENDING", "pseudonym": doc}, note="x")

    def test_serious_ae_threshold_auto_stops(self):
        engine = AuditEngine(Repository())
        self._two_14_requests(engine)
        engine.report_adverse_outcome("REQ-AE-1", ATTENDING, "serious", "AE-X",
                                      reported_at="2026-09-15T09:00:00+08:00")
        engine.report_adverse_outcome("REQ-AE-2", ATTENDING, "serious", "AE-X",
                                      reported_at="2026-09-15T10:00:00+08:00")
        stops = engine.events.of_type("VERSION_STOPPED")
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0].payload["version"], "1.4.0")

        # 停用后 1.4.0 机构新请求被确定性驳回
        later = base_request(
            request_uid="REQ-AFTER-STOP", org_id="ORG-LH-MH",
            consent_id="CON-2026-0001",
            occurred_at="2026-09-20T09:00:00+08:00")
        result = engine.submit(later)
        self.assertEqual(result["rejection"]["reason_code"], "VERSION_STOPPED")

    def test_manual_stop_permission(self):
        engine = AuditEngine(Repository())
        with self.assertRaises(PermissionError):
            engine.stop_version("1.4.0", ATTENDING, "无权停用", "2026-09-20T09:00:00+08:00")
        engine.stop_version("1.4.0",
                            {"role": "ROLE-MEDICAL-ADMIN", "pseudonym": "ADMIN-1"},
                            "医务处停用", "2026-09-20T09:00:00+08:00")
        self.assertIsNotNone(engine.registry.is_stopped("1.4.0"))


class ReplayTest(unittest.TestCase):
    def test_replay_matches_and_distinguishes_model_doctor(self):
        engine = AuditEngine(Repository())
        engine.submit(base_request())
        engine.confirm("REQ-TEST-0001", ATTENDING, note="遵嘱")
        replay = engine.replay("REQ-TEST-0001")
        self.assertEqual(replay["replay_status"], "matches")
        self.assertTrue(replay["chain_verified"])
        self.assertEqual(replay["model_vs_doctor"]["doctor_action"], "confirmed")

    def test_compare_versions_explains_difference(self):
        engine = AuditEngine(Repository())
        engine.submit(base_request())
        cmp = engine.compare_versions("REQ-TEST-0001", ["1.4.0", "1.5.0"])
        self.assertEqual(cmp["outputs"]["1.4.0"]["recommended_formula_codes"],
                         ["FM-YPGF", "FM-SJZT"])
        self.assertEqual(cmp["outputs"]["1.5.0"]["recommended_formula_codes"],
                         ["FM-YPGF"])
        diff = cmp["differences"]["1.5.0"]["rule_change"]
        self.assertEqual(diff["base_policy"], "defense_first")
        self.assertEqual(diff["other_policy"], "spleen_first")

    def test_replay_uses_stopped_version_still_runs(self):
        # 已停用版本的历史决定仍须可重放
        engine = AuditEngine(Repository())
        engine.submit(base_request(org_id="ORG-LH-MH", request_uid="REQ-H-1",
                                   consent_id="CON-2026-0001"))
        engine.stop_version("1.4.0",
                            {"role": "ROLE-MEDICAL-ADMIN", "pseudonym": "ADMIN-1"},
                            "停用", "2026-09-20T09:00:00+08:00")
        replay = engine.replay("REQ-H-1")
        self.assertEqual(replay["replay_status"], "matches")

    def test_history_replays_at_consent_snapshot_even_after_withdrawal(self):
        # 撤回只影响之后的新请求；历史决定按请求时刻的授权快照重放
        engine = AuditEngine(Repository())
        req = base_request(request_uid="REQ-BEFORE-WD", consent_id="CON-2026-0001")
        engine.submit(req)
        engine.withdraw_consent(
            "CON-2026-0001", {"role": "ROLE-CONSULT-DESK", "pseudonym": "DESK"},
            at="2026-09-21T09:00:00+08:00", reason="患者撤回")
        replay = engine.replay("REQ-BEFORE-WD")
        self.assertEqual(replay["replay_status"], "matches")
        self.assertFalse(replay["consent_snapshot"]["withdrawn"])  # 当时未撤回

        later = dict(req)
        later["request_uid"] = "REQ-AFTER-WD"
        later["occurred_at"] = "2026-09-22T09:00:00+08:00"
        blocked = engine.submit(later)
        self.assertEqual(blocked["rejection"]["reason_code"], "CONSENT_DENIED")


if __name__ == "__main__":
    unittest.main()
