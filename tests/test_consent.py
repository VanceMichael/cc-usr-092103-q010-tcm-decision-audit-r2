import unittest

from helpers import make_system
from src.errors import ValidationError


class ConsentTest(unittest.TestCase):
    def setUp(self):
        self.env = make_system()

    def test_grant_allows_purpose(self):
        self.env.consent.grant(patient="pat-1", purposes=("clinical", "research"),
                               share_scope="consortium", at="2026-01-01T00:00:00Z",
                               by="pat-1")
        self.assertTrue(self.env.consent.allows("pat-1", "clinical", "2026-01-02T00:00:00Z"))
        self.assertTrue(self.env.consent.allows("pat-1", "research", "2026-01-02T00:00:00Z"))
        self.assertFalse(self.env.consent.allows("pat-1", "teaching", "2026-01-02T00:00:00Z"))

    def test_no_grant_means_no_purpose(self):
        self.assertFalse(self.env.consent.allows("pat-x", "clinical", "2026-01-02T00:00:00Z"))

    def test_withdraw_single_purpose(self):
        self.env.consent.grant(patient="pat-1", purposes=("clinical", "research"),
                               share_scope="consortium", at="2026-01-01T00:00:00Z", by="pat-1")
        self.env.consent.withdraw(patient="pat-1", purposes=("research",),
                                  at="2026-03-01T00:00:00Z", by="pat-1")
        self.assertTrue(self.env.consent.allows("pat-1", "clinical", "2026-03-02T00:00:00Z"))
        self.assertFalse(self.env.consent.allows("pat-1", "research", "2026-03-02T00:00:00Z"))

    def test_withdraw_all_clears_share_scope(self):
        self.env.consent.grant(patient="pat-1", purposes=("clinical",),
                               share_scope="consortium", at="2026-01-01T00:00:00Z", by="pat-1")
        self.env.consent.withdraw(patient="pat-1", at="2026-03-01T00:00:00Z", by="pat-1")
        state = self.env.consent.state_at("pat-1", "2026-03-02T00:00:00Z")
        self.assertEqual(state["purposes"], [])
        self.assertEqual(state["share_scope"], "none")

    def test_withdrawal_does_not_rewrite_history(self):
        self.env.consent.grant(patient="pat-1", purposes=("clinical",),
                               share_scope="consortium", at="2026-01-01T00:00:00Z", by="pat-1")
        self.env.consent.withdraw(patient="pat-1", at="2026-03-01T00:00:00Z", by="pat-1")
        # 撤回之前的"当时"仍然有效
        self.assertTrue(self.env.consent.allows("pat-1", "clinical", "2026-02-01T00:00:00Z"))

    def test_unknown_purpose_rejected(self):
        with self.assertRaises(ValidationError):
            self.env.consent.grant(patient="pat-1", purposes=("marketing",),
                                   share_scope="none", at="2026-01-01T00:00:00Z", by="pat-1")


if __name__ == "__main__":
    unittest.main()
