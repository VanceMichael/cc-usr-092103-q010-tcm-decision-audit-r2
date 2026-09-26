import unittest

from helpers import grant, infer, make_system
from src.errors import ConflictError


class BackfillTest(unittest.TestCase):
    def setUp(self):
        self.env = make_system()
        grant(self.env.consent)

    def test_offline_backfill_records_original_time(self):
        rec = infer(self.env, "req-off", at="2026-01-10T00:00:00Z",
                    recorded_at="2026-02-01T00:00:00Z", offline=True)
        self.assertTrue(rec.offline)
        self.assertEqual(rec.occurred_at, "2026-01-10T00:00:00Z")
        self.assertEqual(rec.recorded_at, "2026-02-01T00:00:00Z")

    def test_backfill_retry_is_idempotent(self):
        first = infer(self.env, "req-off", at="2026-01-10T00:00:00Z",
                      recorded_at="2026-02-01T00:00:00Z", offline=True)
        again = infer(self.env, "req-off", at="2026-01-10T00:00:00Z",
                      recorded_at="2026-02-05T00:00:00Z", offline=True)
        self.assertEqual(first.decision_id, again.decision_id)
        self.assertEqual(len(self.env.log.of_type("INFERENCE_COMPLETED")), 1)

    def test_backfill_with_different_content_conflicts(self):
        infer(self.env, "req-off", at="2026-01-10T00:00:00Z", offline=True)
        with self.assertRaises(ConflictError):
            infer(self.env, "req-off", at="2026-01-10T00:00:00Z",
                  features={"pulse": "沉迟"}, offline=True)

    def test_backfilled_adverse_events_join_historical_snapshots(self):
        rec = infer(self.env, "req-x", at="2026-04-01T00:00:00Z")
        self.assertEqual(rec.model_version, "fz-2.0")
        self.assertTrue(rec.permission["permitted"])
        # 事后补传 3 条发生在决策之前的不良结果上报（fz-2.0 停用阈值为 3）
        for i in range(3):
            self.env.disposition.report_adverse(
                decision_id=rec.decision_id, institution_id="LH-XH",
                model_version="fz-2.0", doctor_id="doc-1",
                at=f"2026-03-2{i+1}T00:00:00Z",
                recorded_at="2026-04-02T00:00:00Z",
                description="迟报不良事件", severity="中度")
        # 补传改写了当时的权限快照，重放把不一致暴露给审计
        report = self.env.replay.replay(rec.decision_id)
        self.assertFalse(report["checks"]["permission_match"])
        self.assertFalse(report["consistent"])
        # 阈值达到后，同一版本新的推理被确定性地拒绝
        snap = self.env.registry.permission_snapshot(
            "LH-XH", "fz-2.0", "DIS-GM", "2026-04-10T00:00:00Z", self.env.log.events)
        self.assertFalse(snap["permitted"])
        self.assertIn("不良事件达到停用阈值", snap["reasons"])
        # 而补传事件发生时点之前的快照不受影响
        before = self.env.registry.permission_snapshot(
            "LH-XH", "fz-2.0", "DIS-GM", "2026-03-20T00:00:00Z", self.env.log.events)
        self.assertTrue(before["permitted"])


if __name__ == "__main__":
    unittest.main()
