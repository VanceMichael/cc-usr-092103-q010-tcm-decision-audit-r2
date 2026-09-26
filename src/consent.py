"""患者授权：用途（临床/科研/教学）与共享范围，可撤回，历史可重放。"""

from __future__ import annotations

from .errors import ValidationError
from .events import sorted_events_up_to
from .util import canonical_json, sha256_hex

PURPOSES = ("clinical", "research", "teaching")
SHARE_SCOPES = ("none", "institution", "consortium")


class ConsentService:
    def __init__(self, log):
        self._log = log

    def grant(self, *, patient, purposes, share_scope, at, by, recorded_at=None):
        purposes = tuple(sorted(purposes))
        unknown = set(purposes) - set(PURPOSES)
        if unknown:
            raise ValidationError(f"未知用途: {sorted(unknown)}")
        if share_scope not in SHARE_SCOPES:
            raise ValidationError(f"未知共享范围: {share_scope}")
        payload = {"patient": patient, "purposes": list(purposes), "share_scope": share_scope}
        return self._log.append(
            event_id="consent-" + sha256_hex(canonical_json(payload) + at)[:16],
            type="CONSENT_GRANTED", occurred_at=at, recorded_at=recorded_at or at,
            actor=by, payload=payload)

    def withdraw(self, *, patient, purposes=None, at, by, recorded_at=None):
        purposes = tuple(sorted(purposes)) if purposes else list(PURPOSES)
        unknown = set(purposes) - set(PURPOSES)
        if unknown:
            raise ValidationError(f"未知用途: {sorted(unknown)}")
        payload = {"patient": patient, "purposes": list(purposes)}
        return self._log.append(
            event_id="withdraw-" + sha256_hex(canonical_json(payload) + at)[:16],
            type="CONSENT_WITHDRAWN", occurred_at=at, recorded_at=recorded_at or at,
            actor=by, payload=payload)

    def state_at(self, patient, at) -> dict:
        """at 时刻的授权状态；撤回只影响其后的时刻。"""
        purposes = set()
        share_scope = "none"
        for e in sorted_events_up_to(self._log.events, at):
            payload = e.payload
            if payload.get("patient") != patient:
                continue
            if e.type == "CONSENT_GRANTED":
                purposes.update(payload["purposes"])
                share_scope = payload["share_scope"]
            elif e.type == "CONSENT_WITHDRAWN":
                purposes.difference_update(payload["purposes"])
                if not purposes:
                    share_scope = "none"
        return {"patient": patient, "purposes": sorted(purposes), "share_scope": share_scope}

    def allows(self, patient, purpose, at) -> bool:
        return purpose in self.state_at(patient, at)["purposes"]
