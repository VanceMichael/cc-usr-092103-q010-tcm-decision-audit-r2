import json
import unittest

from helpers import FIXTURES, grant, infer, make_system
from src.errors import (ConflictError, ConsentError, DirectIdentifierError,
                        PermissionDenied, RoleError)
from src.gray import bucket


class InferenceTest(unittest.TestCase):
    def setUp(self):
        self.env = make_system()
        grant(self.env.consent)

    def test_happy_path_records_full_context(self):
        rec = infer(self.env, "req-1")
        self.assertEqual(rec.model_version, "fz-1.0")
        self.assertEqual(rec.model_output["formula_code"], "FM-YQS")
        self.assertEqual(rec.model_output["experience_code"], "EXP-GU")
        self.assertTrue(rec.permission["permitted"])
        self.assertEqual(rec.consent["purposes"], ["clinical"])
        self.assertEqual(len(self.env.log.of_type("INFERENCE_COMPLETED")), 1)

    def test_features_are_minimized(self):
        rec = infer(self.env, "req-1",
                    features={"nickname": "小李", "age": 37, "gps": "31.2,121.4"})
        self.assertNotIn("nickname", rec.features)
        self.assertNotIn("age", rec.features)
        self.assertNotIn("gps", rec.features)

    def test_direct_identifier_rejected(self):
        with self.assertRaises(DirectIdentifierError):
            infer(self.env, "req-1", features={"name": "张某"})

    def test_duplicate_request_returns_same_record(self):
        first = infer(self.env, "req-1")
        again = infer(self.env, "req-1")
        self.assertEqual(first.decision_id, again.decision_id)
        self.assertEqual(len(self.env.log.of_type("INFERENCE_COMPLETED")), 1)

    def test_same_request_id_with_different_content_conflicts(self):
        infer(self.env, "req-1")
        with self.assertRaises(ConflictError):
            infer(self.env, "req-1", features={"symptoms": ["咳嗽"]})

    def test_consent_required(self):
        with self.assertRaises(ConsentError):
            infer(self.env, "req-1", patient="pat-no-consent")
        denied = self.env.log.of_type("INFERENCE_DENIED")
        self.assertEqual(len(denied), 1)
        self.assertIn("未授权", denied[0].payload["reason"])

    def test_disease_out_of_scope_denied(self):
        with self.assertRaises(PermissionDenied) as ctx:
            infer(self.env, "req-1", institution="LH-PD", disease="DIS-ZF")
        self.assertIn("病种不在适用范围", str(ctx.exception))

    def test_expired_version_denied(self):
        with self.assertRaises(PermissionDenied) as ctx:
            infer(self.env, "req-1", institution="LH-PD", at="2026-05-02T00:00:00Z")
        self.assertIn("已过停用日期", str(ctx.exception))

    def test_gray_switch_is_deterministic(self):
        self.env.gray.set_rules(
            institution_id="LH-XH",
            rules=[{"model_version": "fz-2.0", "percent": 100,
                    "effective_from": "2026-03-15T00:00:00Z"}],
            at="2026-03-15T00:00:00Z", by="ops-1")
        rec = infer(self.env, "req-g1", at="2026-04-01T00:00:00Z")
        self.assertEqual(rec.model_version, "fz-2.0")
        self.assertEqual(rec.gray["bucket"], bucket("LH-XH", "req-g1"))
        report = self.env.replay.replay(rec.decision_id)
        self.assertTrue(report["checks"]["version_match"])
        self.assertTrue(report["checks"]["gray_match"])

    def test_gray_switch_back(self):
        self.env.gray.set_rules(
            institution_id="LH-XH",
            rules=[{"model_version": "fz-2.0", "percent": 100,
                    "effective_from": "2026-03-15T00:00:00Z"}],
            at="2026-03-15T00:00:00Z", by="ops-1")
        self.env.gray.set_rules(
            institution_id="LH-XH",
            rules=[{"model_version": "fz-1.0", "percent": 100,
                    "effective_from": "2026-05-01T00:00:00Z"}],
            at="2026-05-01T00:00:00Z", by="ops-1")
        rec = infer(self.env, "req-g2", at="2026-05-02T00:00:00Z")
        self.assertEqual(rec.model_version, "fz-1.0")

    def test_gray_rule_for_unregistered_version_skipped(self):
        self.env.gray.set_rules(
            institution_id="LH-XH",
            rules=[{"model_version": "fz-9.9", "percent": 100,
                    "effective_from": "2026-01-15T00:00:00Z"}],
            at="2026-01-15T00:00:00Z", by="ops-1")
        rec = infer(self.env, "req-g3")
        self.assertEqual(rec.model_version, "fz-1.0")

    def test_gray_manage_requires_role(self):
        with self.assertRaises(RoleError):
            self.env.gray.set_rules(institution_id="LH-XH", rules=[],
                                    at="2026-03-15T00:00:00Z", by="doc-1",
                                    by_role="physician")

    def test_infer_requires_role(self):
        with self.assertRaises(RoleError):
            infer(self.env, "req-1", doctor_role="researcher")

    def test_fixture_sample_request(self):
        req = json.loads((FIXTURES / "inference_request.json").read_text(encoding="utf-8"))
        grant(self.env.consent, patient=req["patient_pseudonym"])
        rec = self.env.inference.infer(
            request_id=req["request_id"], institution_id=req["institution_id"],
            doctor_id=req["doctor_id"], patient_pseudonym=req["patient_pseudonym"],
            disease_code=req["disease_code"], features=req["features"],
            at="2026-02-01T00:00:00Z")
        self.assertEqual(rec.model_output["formula_code"], "FM-YQS")


if __name__ == "__main__":
    unittest.main()
