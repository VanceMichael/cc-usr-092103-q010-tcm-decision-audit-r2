import unittest

from helpers import grant, infer, make_system
from src.errors import ConflictError, ValidationError


class DispositionTest(unittest.TestCase):
    def setUp(self):
        self.env = make_system()
        grant(self.env.consent)
        self.rec = infer(self.env, "req-1")

    def test_confirm(self):
        self.env.disposition.confirm(decision_id=self.rec.decision_id,
                                     doctor_id="doc-1", at="2026-02-01T01:00:00Z",
                                     note="饭后温服")
        disp = self.env.disposition.disposition_for(self.rec.decision_id)
        self.assertEqual(disp["status"], "confirmed")
        self.assertEqual(disp["note"], "饭后温服")

    def test_reject_requires_reason(self):
        with self.assertRaises(ValidationError):
            self.env.disposition.reject(decision_id=self.rec.decision_id,
                                        doctor_id="doc-1",
                                        at="2026-02-01T01:00:00Z", reason="")
        self.env.disposition.reject(decision_id=self.rec.decision_id,
                                    doctor_id="doc-1",
                                    at="2026-02-01T01:00:00Z", reason="患者已自愈")
        self.assertEqual(self.env.disposition.disposition_for(self.rec.decision_id)["status"],
                         "rejected")

    def test_revise_validates_formula(self):
        with self.assertRaises(ValidationError):
            self.env.disposition.revise(decision_id=self.rec.decision_id,
                                        doctor_id="doc-1",
                                        at="2026-02-01T01:00:00Z",
                                        new_formula_code="FM-XXX")
        self.env.disposition.revise(decision_id=self.rec.decision_id,
                                    doctor_id="doc-1",
                                    at="2026-02-01T01:00:00Z",
                                    new_formula_code="FM-GZT", note="改从桂枝汤")
        disp = self.env.disposition.disposition_for(self.rec.decision_id)
        self.assertEqual(disp["status"], "revised")
        self.assertEqual(disp["final_formula_code"], "FM-GZT")

    def test_final_disposition_is_immutable(self):
        self.env.disposition.confirm(decision_id=self.rec.decision_id,
                                     doctor_id="doc-1", at="2026-02-01T01:00:00Z")
        with self.assertRaises(ConflictError):
            self.env.disposition.reject(decision_id=self.rec.decision_id,
                                        doctor_id="doc-1",
                                        at="2026-02-01T02:00:00Z", reason="改主意")
        # 相同内容重复提交幂等
        again = self.env.disposition.confirm(decision_id=self.rec.decision_id,
                                             doctor_id="doc-1",
                                             at="2026-02-01T01:00:00Z")
        self.assertEqual(len(self.env.log.of_type("DISPOSITION_CONFIRMED")), 1)
        self.assertIsNotNone(again)

    def test_adverse_reports_accumulate(self):
        for i, at in enumerate(("2026-02-02T00:00:00Z", "2026-02-03T00:00:00Z")):
            self.env.disposition.report_adverse(
                decision_id=self.rec.decision_id, institution_id="LH-XH",
                model_version="fz-1.0", doctor_id="doc-1", at=at,
                description=f"胃脘不适{i+1}", severity="轻度")
        disp = self.env.disposition.disposition_for(self.rec.decision_id)
        self.assertEqual(len(disp["adverse_reports"]), 2)

    def test_adverse_reports_feed_deactivation_threshold(self):
        rec = infer(self.env, "req-2", at="2026-04-01T00:00:00Z")
        for i in range(3):
            self.env.disposition.report_adverse(
                decision_id=rec.decision_id, institution_id="LH-XH",
                model_version="fz-2.0", doctor_id="doc-1",
                at=f"2026-04-0{i+2}T00:00:00Z", description="皮疹", severity="中度")
        snap = self.env.registry.permission_snapshot(
            "LH-XH", "fz-2.0", "DIS-GM", "2026-04-10T00:00:00Z", self.env.log.events)
        self.assertFalse(snap["permitted"])
        self.assertIn("不良事件达到停用阈值", snap["reasons"])


if __name__ == "__main__":
    unittest.main()
