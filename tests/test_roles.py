import unittest

from helpers import FIXTURES
from src.errors import NotFoundError, RoleError
from src.roles import Roles


class RolesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roles = Roles.load(FIXTURES / "roles.json")

    def test_permissions_loaded(self):
        self.assertIn("infer", self.roles.permissions("physician"))
        self.assertIn("consult:resolve", self.roles.permissions("senior_physician"))
        self.assertNotIn("consult:resolve", self.roles.permissions("physician"))

    def test_require_raises(self):
        with self.assertRaises(RoleError):
            self.roles.require("researcher", "infer")
        self.roles.require("auditor", "audit:replay")

    def test_unknown_role(self):
        with self.assertRaises(NotFoundError):
            self.roles.permissions("nobody")


if __name__ == "__main__":
    unittest.main()
