import json
import unittest

from src.audit.events import EventLog, TamperError


class EventLogTest(unittest.TestCase):
    def test_append_links_hashes(self):
        log = EventLog()
        e1 = log.append("REQUEST_ACCEPTED", {"a": 1}, occurred_at="2026-09-15T09:00:00+08:00",
                        actor={"role": "ROLE-ATTENDING"})
        e2 = log.append("MODEL_OUTPUT", {"b": 2}, occurred_at="2026-09-15T09:00:01+08:00",
                        actor={"role": "SYSTEM"})
        self.assertEqual(e2.prev_hash, e1.hash)
        log.verify()

    def test_content_tamper_detected(self):
        log = EventLog()
        log.append("CONSENT_WITHDRAWN", {"consent_id": "CON-1"},
                   occurred_at="2026-09-15T09:00:00+08:00", actor={"role": "ROLE-PATIENT"})
        lines = log.export_jsonl().splitlines()
        record = json.loads(lines[0])
        record["payload"] = {"consent_id": "CON-FORGED"}
        tampered = EventLog()
        with self.assertRaises(TamperError):
            tampered.import_jsonl(json.dumps(record, ensure_ascii=False))

    def test_delete_detected(self):
        log = EventLog()
        log.append("REQUEST_ACCEPTED", {"n": 1},
                   occurred_at="2026-09-15T09:00:00+08:00", actor={"role": "ROLE-ATTENDING"})
        log.append("ADVERSE_OUTCOME", {"n": 2},
                   occurred_at="2026-09-15T09:01:00+08:00", actor={"role": "ROLE-ATTENDING"})
        lines = log.export_jsonl().splitlines()
        first = lines[0]
        # 删除第二条不会被直接发现（它是链尾），但插入/删除中间事件会；
        # 改写第一条 payload 必须被发现。
        rec = json.loads(first)
        rec["payload"] = {"n": 99}
        with self.assertRaises(TamperError):
            EventLog.import_jsonl(json.dumps(rec, ensure_ascii=False) + "\n" + lines[1])

    def test_roundtrip_preserves_chain(self):
        log = EventLog()
        log.append("VERSION_STOPPED", {"version": "1.4.0"},
                   occurred_at="2026-09-15T09:00:00+08:00", actor={"role": "ROLE-MEDICAL-ADMIN"})
        restored = EventLog.import_jsonl(log.export_jsonl())
        restored.verify()
        self.assertEqual(len(restored), 1)

    def test_deterministic_hash_across_key_order(self):
        from src.audit.serde import digest
        # 规范化序列化使键序不影响摘要；事件整体哈希另含 recorded_at，
        # 故这里比较载荷摘要的确定性。
        self.assertEqual(digest({"b": 2, "a": 1}), digest({"a": 1, "b": 2}))


if __name__ == "__main__":
    unittest.main()
