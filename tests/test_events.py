import unittest
from dataclasses import FrozenInstanceError

from src.errors import ConflictError, ValidationError
from src.events import EventLog


def append(log, event_id="e1", type="CONSENT_GRANTED",
           occurred="2026-01-01T00:00:00Z", recorded=None, payload=None):
    return log.append(event_id=event_id, type=type, occurred_at=occurred,
                      recorded_at=recorded or occurred, actor="tester",
                      payload=payload if payload is not None else {"k": "v"})


class EventLogTest(unittest.TestCase):
    def test_append_and_verify(self):
        log = EventLog()
        append(log, "e1")
        append(log, "e2", occurred="2026-01-02T00:00:00Z")
        self.assertEqual(len(log.events), 2)
        self.assertTrue(log.verify())

    def test_event_is_frozen(self):
        log = EventLog()
        event = append(log)
        with self.assertRaises(FrozenInstanceError):
            event.type = "CONSENT_WITHDRAWN"

    def test_log_exposes_immutable_tuple(self):
        log = EventLog()
        append(log)
        self.assertIsInstance(log.events, tuple)
        with self.assertRaises(TypeError):
            log.events[0] = None

    def test_tamper_is_detected(self):
        log = EventLog()
        append(log, "e1")
        append(log, "e2", occurred="2026-01-02T00:00:00Z")
        log._events[0].payload["k"] = "被篡改"
        self.assertFalse(log.verify())

    def test_duplicate_identical_is_idempotent(self):
        log = EventLog()
        first = append(log, "e1", recorded="2026-02-01T00:00:00Z")
        again = append(log, "e1", recorded="2026-02-02T00:00:00Z")
        self.assertIs(first, again)
        self.assertEqual(len(log.events), 1)

    def test_duplicate_with_different_content_conflicts(self):
        log = EventLog()
        append(log, "e1")
        with self.assertRaises(ConflictError):
            append(log, "e1", payload={"k": "别的内容"})

    def test_unknown_type_rejected(self):
        log = EventLog()
        with self.assertRaises(ValidationError):
            append(log, type="NOT_A_TYPE")

    def test_up_to_folds_by_occurred_time(self):
        log = EventLog()
        append(log, "e2", occurred="2026-01-05T00:00:00Z")
        append(log, "e1", occurred="2026-01-03T00:00:00Z",
               recorded="2026-01-06T00:00:00Z")  # 离线补传
        self.assertEqual([e.event_id for e in log.up_to("2026-01-04T00:00:00Z")], ["e1"])
        self.assertEqual([e.event_id for e in log.up_to("2026-01-06T00:00:00Z")],
                         ["e1", "e2"])


if __name__ == "__main__":
    unittest.main()
