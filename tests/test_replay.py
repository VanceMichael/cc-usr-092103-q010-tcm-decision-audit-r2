import unittest

from helpers import grant, infer, make_system
from src.errors import NotFoundError


def two_version_scenario(env):
    """同一病例：灰度切换前后各决策一次，得到两个版本的建议。"""
    grant(env.consent, patient="pat-1")
    rec_a = infer(env, "req-A", at="2026-02-01T00:00:00Z")   # fz-1.0
    env.gray.set_rules(
        institution_id="LH-XH",
        rules=[{"model_version": "fz-2.0", "percent": 100,
                "effective_from": "2026-03-15T00:00:00Z"}],
        at="2026-03-15T00:00:00Z", by="ops-1")
    rec_b = infer(env, "req-B", at="2026-04-01T00:00:00Z")   # fz-2.0
    return rec_a, rec_b


class ReplayTest(unittest.TestCase):
    def setUp(self):
        self.env = make_system()

    def test_same_case_two_versions_explained(self):
        rec_a, rec_b = two_version_scenario(self.env)
        self.assertEqual(rec_a.model_version, "fz-1.0")
        self.assertEqual(rec_b.model_version, "fz-2.0")

        report = self.env.replay.explain_difference(rec_a.decision_id, rec_b.decision_id)
        kinds = [r["kind"] for r in report["reasons"]]
        self.assertIn("model_version", kinds)
        reason = next(r for r in report["reasons"] if r["kind"] == "model_version")
        self.assertIn("fz-1.0", reason["detail"])
        self.assertIn("fz-2.0", reason["detail"])
        self.assertIn("顾氏外感热病诊疗经验 v1", reason["experience_a"])
        self.assertIn("顾氏外感热病诊疗经验 v2", reason["experience_b"])
        self.assertEqual(report["a"]["model_output"]["formula_code"], "FM-YQS")
        self.assertEqual(report["b"]["model_output"]["formula_code"], "FM-YQSJJ")
        self.assertIn("医生", report["note"])

    def test_replay_is_consistent(self):
        rec_a, rec_b = two_version_scenario(self.env)
        for rec in (rec_a, rec_b):
            report = self.env.replay.replay(rec.decision_id)
            self.assertTrue(report["consistent"], msg=report["checks"])

    def test_replay_distinguishes_model_output_and_doctor_disposition(self):
        rec_a, rec_b = two_version_scenario(self.env)
        self.env.disposition.confirm(decision_id=rec_a.decision_id, doctor_id="doc-1",
                                     at="2026-02-01T01:00:00Z", note="照方")
        self.env.disposition.revise(decision_id=rec_b.decision_id, doctor_id="doc-1",
                                    at="2026-04-01T01:00:00Z",
                                    new_formula_code="FM-GZT", note="改从桂枝汤")
        report_a = self.env.replay.replay(rec_a.decision_id)
        self.assertEqual(report_a["model_output"]["formula_code"], "FM-YQS")
        self.assertEqual(report_a["doctor_disposition"]["status"], "confirmed")
        report_b = self.env.replay.replay(rec_b.decision_id)
        self.assertEqual(report_b["model_output"]["formula_code"], "FM-YQSJJ")
        self.assertEqual(report_b["doctor_disposition"]["status"], "revised")
        self.assertEqual(report_b["doctor_disposition"]["final_formula_code"], "FM-GZT")

    def test_replay_uses_snapshots_at_decision_time(self):
        rec_a, _ = two_version_scenario(self.env)
        # 决策之后发生的变化不应改变"当时"的重放
        self.env.consent.withdraw(patient="pat-1", at="2026-05-01T00:00:00Z", by="pat-1")
        self.env.admin.deactivate_version(institution_id="LH-XH", model_version="fz-1.0",
                                          reason="版本下線", at="2026-05-01T00:00:00Z",
                                          by="ops-1")
        report = self.env.replay.replay(rec_a.decision_id)
        self.assertTrue(report["consistent"], msg=report["checks"])
        self.assertEqual(report["context"]["consent"]["purposes"], ["clinical"])
        self.assertTrue(report["context"]["permission"]["permitted"])

    def test_tamper_breaks_consistency(self):
        rec_a, _ = two_version_scenario(self.env)
        for event in self.env.log.events:
            if event.payload.get("decision_id") == rec_a.decision_id:
                event.payload["model_output"]["formula_code"] = "FM-XXX"
        report = self.env.replay.replay(rec_a.decision_id)
        self.assertFalse(report["checks"]["model_output_match"])
        self.assertFalse(report["checks"]["chain_intact"])
        self.assertFalse(report["consistent"])
        self.assertFalse(self.env.log.verify())

    def test_unknown_decision(self):
        with self.assertRaises(NotFoundError):
            self.env.replay.replay("dec-不存在")


if __name__ == "__main__":
    unittest.main()
