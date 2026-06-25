"""
Tests for core/event.py dataclasses:
  KnownIssuePattern, Diagnosis, RemediationOutcome
"""

import pytest
from env_healing_agent.core.event import (
    Diagnosis,
    Issue,
    KnownIssuePattern,
    LogLine,
    RemediationOutcome,
    Severity,
)


# ── KnownIssuePattern ─────────────────────────────────────────────────────────

class TestKnownIssuePattern:

    def _sample_dict(self, **overrides):
        base = {
            "type": "api_rate_limit",
            "pattern": "HTTP.*429",
            "severity": "low",
            "auto_fix": True,
            "recommended_fix": "backoff_and_retry",
            "description": "API rate limit hit",
            "symptoms": ["429 errors"],
            "common_causes": ["too many calls"],
            "learned_confidence": 0.9,
            "last_adjusted": "2026-01-01T00:00:00",
            "adjustment_reason": "3 successes",
        }
        base.update(overrides)
        return base

    def test_from_dict_all_fields(self):
        p = KnownIssuePattern.from_dict(self._sample_dict())
        assert p.type == "api_rate_limit"
        assert p.severity == Severity.LOW
        assert p.auto_fix is True
        assert p.recommended_fix == "backoff_and_retry"
        assert p.learned_confidence == 0.9
        assert p.last_adjusted == "2026-01-01T00:00:00"
        assert p.adjustment_reason == "3 successes"

    def test_from_dict_optional_fields_absent(self):
        d = self._sample_dict()
        del d["recommended_fix"]
        del d["last_adjusted"]
        del d["adjustment_reason"]
        p = KnownIssuePattern.from_dict(d)
        assert p.recommended_fix is None
        assert p.last_adjusted is None
        assert p.adjustment_reason is None

    def test_from_dict_invalid_severity_defaults_to_medium(self):
        p = KnownIssuePattern.from_dict(self._sample_dict(severity="nonsense"))
        assert p.severity == Severity.MEDIUM

    def test_to_dict_round_trip(self):
        p = KnownIssuePattern.from_dict(self._sample_dict())
        d = p.to_dict()
        p2 = KnownIssuePattern.from_dict(d)
        assert p2.type == p.type
        assert p2.severity == p.severity
        assert p2.recommended_fix == p.recommended_fix
        assert p2.learned_confidence == p.learned_confidence

    def test_to_dict_omits_none_optionals(self):
        d = self._sample_dict()
        del d["recommended_fix"]
        del d["last_adjusted"]
        del d["adjustment_reason"]
        p = KnownIssuePattern.from_dict(d)
        out = p.to_dict()
        assert "recommended_fix" not in out
        assert "last_adjusted" not in out
        assert "adjustment_reason" not in out

    def test_matches_returns_true_on_match(self):
        p = KnownIssuePattern.from_dict(self._sample_dict(pattern="HTTP.*429"))
        assert p.matches("Request failed: HTTP status 429") is True

    def test_matches_is_case_insensitive(self):
        p = KnownIssuePattern.from_dict(self._sample_dict(pattern="rate.limit.exceed"))
        assert p.matches("RATE LIMIT EXCEEDED") is True

    def test_matches_returns_false_on_no_match(self):
        p = KnownIssuePattern.from_dict(self._sample_dict(pattern="HTTP.*429"))
        assert p.matches("everything is fine") is False

    def test_all_severity_values(self):
        for sev in ("low", "medium", "high", "critical"):
            p = KnownIssuePattern.from_dict(self._sample_dict(severity=sev))
            assert p.severity.value == sev


# ── Diagnosis ─────────────────────────────────────────────────────────────────

class TestDiagnosis:

    def _sample(self, **overrides):
        defaults = dict(
            issue_type="api_rate_limit",
            root_cause="Too many requests",
            confidence=0.9,
            severity=Severity.LOW,
            evidence=["429 seen"],
            recommended_fix="backoff_and_retry",
            fix_parameters={"backoff_seconds": 60},
        )
        defaults.update(overrides)
        return Diagnosis(**defaults)

    def test_to_dict_contains_all_keys(self):
        d = self._sample().to_dict()
        assert set(d.keys()) == {
            "issue_type", "root_cause", "confidence", "severity",
            "evidence", "recommended_fix", "fix_parameters",
        }

    def test_to_dict_severity_is_string(self):
        d = self._sample().to_dict()
        assert d["severity"] == "low"

    def test_to_dict_values_correct(self):
        d = self._sample().to_dict()
        assert d["confidence"] == 0.9
        assert d["recommended_fix"] == "backoff_and_retry"
        assert d["fix_parameters"] == {"backoff_seconds": 60}

    def test_to_dict_none_recommended_fix(self):
        d = self._sample(recommended_fix=None).to_dict()
        assert d["recommended_fix"] is None


# ── RemediationOutcome ────────────────────────────────────────────────────────

class TestRemediationOutcome:

    def _sample_dict(self, **overrides):
        base = {
            "timestamp": "2026-05-01T10:00:00",
            "issue_type": "ocm_auth_failure",
            "recommended_fix": "refresh_ocm_token",
            "success": True,
            "confidence_used": 0.98,
            "root_cause": "Token expired",
            "resource_key": "default/my-cluster",
            "details": "",
        }
        base.update(overrides)
        return base

    def test_from_dict_all_fields(self):
        o = RemediationOutcome.from_dict(self._sample_dict())
        assert o.issue_type == "ocm_auth_failure"
        assert o.recommended_fix == "refresh_ocm_token"
        assert o.success is True
        assert o.confidence_used == 0.98
        assert o.root_cause == "Token expired"
        assert o.resource_key == "default/my-cluster"
        assert o.timestamp == "2026-05-01T10:00:00"

    def test_from_dict_missing_optional_fields(self):
        d = {"issue_type": "x", "recommended_fix": "y", "success": False,
             "confidence_used": 0.5, "root_cause": "z"}
        o = RemediationOutcome.from_dict(d)
        assert o.resource_key == ""
        assert o.details == ""

    def test_to_dict_round_trip(self):
        o = RemediationOutcome.from_dict(self._sample_dict())
        d = o.to_dict()
        o2 = RemediationOutcome.from_dict(d)
        assert o2.issue_type == o.issue_type
        assert o2.success == o.success
        assert o2.confidence_used == o.confidence_used
        assert o2.timestamp == o.timestamp

    def test_to_dict_keys(self):
        o = RemediationOutcome.from_dict(self._sample_dict())
        assert set(o.to_dict().keys()) == {
            "timestamp", "issue_type", "recommended_fix", "success",
            "confidence_used", "root_cause", "resource_key", "details",
        }

    def test_default_timestamp_is_set(self):
        o = RemediationOutcome(
            issue_type="x", recommended_fix="y", success=True,
            confidence_used=0.5, root_cause="r",
        )
        assert o.timestamp != ""
        assert "T" in o.timestamp  # ISO format

    def test_success_false(self):
        o = RemediationOutcome.from_dict(self._sample_dict(success=False))
        assert o.success is False
