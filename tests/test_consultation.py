import unittest

from helpers import grant, infer, make_system
from src.errors import ConflictError, ConsentError, NotFoundError, RoleError, ValidationError
from src.minimization import FEATURE_ALLOWLIST


class ConsultationTest(unittest.TestCase):
    def setUp(self):
        self.env = make_system()
        grant(self.env.consent, patient="pat-1")
        self.rec = infer(self.env, "req-1")

    def request(self, **overrides):
        params = dict(consultation_id="consul-1", decision_id=self.rec.decision_id,
                      from_institution="CHS-CF", to_institution="LH-XH",
                      question="寒热往来是否调整方剂", at="2026-02-02T00:00:00Z",
                      by="doc-9")
        params.update(overrides)
        return self.env.consult.request(**params)

    def test_request_and_resolve(self):
        self.request()
        self.assertEqual(self.env.consult.status("consul-1")["state"], "open")
        self.env.consult.resolve(consultation_id="consul-1", by="prof-1",
                                 at="2026-02-03T00:00:00Z",
                                 conclusion="维持原方，三日复诊", formula_code="FM-YQS")
        status = self.env.consult.status("consul-1")
        self.assertEqual(status["state"], "resolved")
        self.assertEqual(status["resolution"]["conclusion"], "维持原方，三日复诊")
        self.assertEqual(status["resolved_by"], "prof-1")

    def test_resolve_is_idempotent(self):
        self.request()
        first = self.env.consult.resolve(consultation_id="consul-1", by="prof-1",
                                         at="2026-02-03T00:00:00Z", conclusion="维持原方")
        again = self.env.consult.resolve(consultation_id="consul-1", by="prof-1",
                                         at="2026-02-03T00:00:00Z", conclusion="维持原方")
        self.assertIs(first, again)
        self.assertEqual(len(self.env.log.of_type("CONSULTATION_RESOLVED")), 1)

    def test_conflicting_resolution_rejected(self):
        self.request()
        self.env.consult.resolve(consultation_id="consul-1", by="prof-1",
                                 at="2026-02-03T00:00:00Z", conclusion="维持原方")
        with self.assertRaises(ConflictError):
            self.env.consult.resolve(consultation_id="consul-1", by="prof-2",
                                     at="2026-02-04T00:00:00Z", conclusion="改用他方")

    def test_share_scope_required(self):
        grant(self.env.consent, patient="pat-2", share_scope="institution")
        rec2 = infer(self.env, "req-2", patient="pat-2")
        with self.assertRaises(ConsentError):
            self.request(consultation_id="consul-2", decision_id=rec2.decision_id)

    def test_same_institution_rejected(self):
        with self.assertRaises(ValidationError):
            self.request(to_institution="CHS-CF")

    def test_shared_payload_is_minimized(self):
        self.request()
        event = self.env.log.of_type("CONSULTATION_REQUESTED")[0]
        shared = event.payload["shared_features"]
        self.assertTrue(set(shared) <= FEATURE_ALLOWLIST)
        self.assertNotIn("patient_pseudonym", event.payload)
        self.assertNotIn("doctor_id", event.payload)

    def test_resolve_requires_existing_request(self):
        with self.assertRaises(NotFoundError):
            self.env.consult.resolve(consultation_id="consul-x", by="prof-1",
                                     at="2026-02-03T00:00:00Z", conclusion="x")

    def test_resolve_requires_senior_role(self):
        self.request()
        with self.assertRaises(RoleError):
            self.env.consult.resolve(consultation_id="consul-1", by="doc-1",
                                     at="2026-02-03T00:00:00Z", conclusion="x",
                                     by_role="physician")


if __name__ == "__main__":
    unittest.main()
