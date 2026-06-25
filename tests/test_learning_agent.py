"""
Tests for learning/learning_agent.py
"""

import json
import pytest
from env_healing_agent.core.event import Diagnosis, RemediationOutcome, Severity
from env_healing_agent.learning.learning_agent import LearningAgent


@pytest.fixture
def agent(kb_dir):
    return LearningAgent(kb_dir=kb_dir, enabled=True, verbose=False)


@pytest.fixture
def disabled_agent(kb_dir):
    return LearningAgent(kb_dir=kb_dir, enabled=False, verbose=False)


def _make_outcome(**overrides):
    defaults = dict(
        issue_type="test_issue",
        recommended_fix="log_and_continue",
        success=True,
        confidence_used=0.8,
        root_cause="A test issue",
        resource_key="default/cluster",
    )
    defaults.update(overrides)
    return RemediationOutcome(**defaults)


# ── record_outcome() ──────────────────────────────────────────────────────────

class TestRecordOutcome:

    def test_appends_to_session_outcomes(self, agent):
        agent.record_outcome("test_issue", "log_and_continue", True, 0.8)
        # _append_outcomes flushes to disk and clears session list
        outcomes = agent._load_all_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].issue_type == "test_issue"

    def test_does_nothing_when_disabled(self, disabled_agent, kb_dir):
        disabled_agent.record_outcome("test_issue", "log_and_continue", True, 0.8)
        assert disabled_agent._load_all_outcomes() == []

    def test_persists_correct_fields(self, agent):
        agent.record_outcome(
            issue_type="ocm_auth_failure",
            fix_applied="refresh_ocm_token",
            success=False,
            confidence=0.75,
            root_cause="token expired",
            resource_key="ns/cluster",
            details="extra info",
        )
        o = agent._load_all_outcomes()[0]
        assert o.issue_type == "ocm_auth_failure"
        assert o.recommended_fix == "refresh_ocm_token"
        assert o.success is False
        assert o.confidence_used == 0.75
        assert o.root_cause == "token expired"
        assert o.resource_key == "ns/cluster"
        assert o.details == "extra info"

    def test_multiple_outcomes_accumulated(self, agent):
        agent.record_outcome("issue_a", "log_and_continue", True, 0.8)
        agent.record_outcome("issue_b", "log_and_continue", False, 0.5)
        all_outcomes = agent._load_all_outcomes()
        assert len(all_outcomes) == 2
        types = {o.issue_type for o in all_outcomes}
        assert types == {"issue_a", "issue_b"}

    def test_outcome_has_timestamp(self, agent):
        agent.record_outcome("test_issue", "log_and_continue", True, 0.8)
        o = agent._load_all_outcomes()[0]
        assert o.timestamp != ""
        assert "T" in o.timestamp


# ── _load_all_outcomes() ──────────────────────────────────────────────────────

class TestLoadAllOutcomes:

    def test_returns_empty_list_when_file_is_empty(self, agent):
        assert agent._load_all_outcomes() == []

    def test_returns_typed_outcomes(self, agent):
        agent.record_outcome("test_issue", "log_and_continue", True, 0.9)
        outcomes = agent._load_all_outcomes()
        assert all(isinstance(o, RemediationOutcome) for o in outcomes)

    def test_loads_from_existing_file(self, kb_dir):
        data = [
            {
                "timestamp": "2026-01-01T00:00:00",
                "issue_type": "existing_issue",
                "recommended_fix": "log_and_continue",
                "success": True,
                "confidence_used": 0.9,
                "root_cause": "known root cause",
                "resource_key": "ns/res",
                "details": "",
            }
        ]
        (kb_dir / "remediation_outcomes.json").write_text(json.dumps(data))
        agent = LearningAgent(kb_dir=kb_dir, enabled=True)
        outcomes = agent._load_all_outcomes()
        assert len(outcomes) == 1
        assert outcomes[0].issue_type == "existing_issue"
        assert outcomes[0].success is True


# ── _calculate_confidence_adjustments() ───────────────────────────────────────

class TestCalculateConfidenceAdjustments:

    def test_boosts_after_consecutive_successes(self, agent):
        outcomes = [_make_outcome(success=True) for _ in range(4)]
        adjustments = agent._calculate_confidence_adjustments(outcomes)
        assert len(adjustments) == 1
        assert adjustments[0]["action"] == "boost"
        assert adjustments[0]["issue_type"] == "test_issue"

    def test_reduces_after_consecutive_failures(self, agent):
        outcomes = [_make_outcome(success=False) for _ in range(3)]
        adjustments = agent._calculate_confidence_adjustments(outcomes)
        assert len(adjustments) == 1
        assert adjustments[0]["action"] == "reduce"
        assert adjustments[0]["delta"] == -0.1

    def test_no_adjustment_for_mixed_results(self, agent):
        outcomes = [
            _make_outcome(success=True),
            _make_outcome(success=False),
            _make_outcome(success=True),
        ]
        adjustments = agent._calculate_confidence_adjustments(outcomes)
        assert adjustments == []

    def test_no_adjustment_for_empty_outcomes(self, agent):
        assert agent._calculate_confidence_adjustments([]) == []

    def test_separate_adjustments_per_issue_type(self, agent):
        outcomes = (
            [_make_outcome(issue_type="issue_a", success=True) for _ in range(4)] +
            [_make_outcome(issue_type="issue_b", success=False) for _ in range(3)]
        )
        adjustments = agent._calculate_confidence_adjustments(outcomes)
        types = {a["issue_type"] for a in adjustments}
        assert types == {"issue_a", "issue_b"}

    def test_only_last_5_outcomes_count(self, agent):
        # 6 successes but only last 5 are evaluated
        outcomes = [_make_outcome(success=True) for _ in range(6)]
        adjustments = agent._calculate_confidence_adjustments(outcomes)
        assert len(adjustments) == 1
        assert adjustments[0]["action"] == "boost"


# ── end_of_run_summary() ──────────────────────────────────────────────────────

class TestEndOfRunSummary:

    def test_returns_empty_summary_with_no_outcomes(self, agent):
        result = agent.end_of_run_summary()
        assert result == {"adjustments": [], "pending_reviews": 0}

    def test_summary_contains_session_count(self, agent):
        agent.record_outcome("test_issue", "log_and_continue", True, 0.8)
        # Load outcomes back since record_outcome flushes them
        agent.session_outcomes = agent._load_all_outcomes()
        result = agent.end_of_run_summary()
        assert result["session_outcomes"] == 1

    def test_summary_fix_stats_aggregated_correctly(self, agent):
        for _ in range(2):
            agent.record_outcome("test_issue", "log_and_continue", True, 0.8)
        agent.record_outcome("test_issue", "log_and_continue", False, 0.8)
        agent.session_outcomes = agent._load_all_outcomes()
        result = agent.end_of_run_summary()
        key = "test_issue:log_and_continue"
        assert key in result["fix_stats"]
        assert result["fix_stats"][key]["successes"] == 2
        assert result["fix_stats"][key]["failures"] == 1


# ── get_learning_stats() ──────────────────────────────────────────────────────

class TestGetLearningStats:

    def test_empty_stats_when_no_outcomes(self, agent):
        stats = agent.get_learning_stats()
        assert stats["total_outcomes"] == 0
        assert stats["fix_stats"] == {}

    def test_success_rate_calculated(self, agent):
        agent.record_outcome("test_issue", "log_and_continue", True, 0.9)
        agent.record_outcome("test_issue", "log_and_continue", True, 0.9)
        agent.record_outcome("test_issue", "log_and_continue", False, 0.9)
        stats = agent.get_learning_stats()
        fix = stats["fix_stats"]["log_and_continue"]
        assert fix["total"] == 3
        assert fix["successes"] == 2
        assert fix["failures"] == 1
        assert fix["success_rate"] == "67%"

    def test_total_outcomes_count(self, agent):
        for _ in range(5):
            agent.record_outcome("test_issue", "log_and_continue", True, 0.9)
        assert agent.get_learning_stats()["total_outcomes"] == 5


# ── suggest_new_pattern() ─────────────────────────────────────────────────────

class TestSuggestNewPattern:

    def test_persists_pending_suggestion(self, agent, kb_dir):
        diagnosis = Diagnosis(
            issue_type="new_issue",
            root_cause="something unexpected",
            confidence=0.4,
            severity=Severity.MEDIUM,
            evidence=["weird log line"],
            recommended_fix="log_and_continue",
        )
        agent.suggest_new_pattern("weird log line", diagnosis, "log_and_continue", False)
        pending = json.loads((kb_dir / "pending_learnings.json").read_text())
        assert len(pending) == 1
        assert pending[0]["suggested_pattern"]["type"] == "new_issue"
        assert pending[0]["suggested_pattern"]["severity"] == "medium"

    def test_uses_diagnosis_attributes(self, agent, kb_dir):
        diagnosis = Diagnosis(
            issue_type="my_issue",
            root_cause="the root cause",
            confidence=0.6,
            severity=Severity.HIGH,
            recommended_fix="log_and_continue",
        )
        agent.suggest_new_pattern("trigger line", diagnosis, "log_and_continue", True)
        pending = json.loads((kb_dir / "pending_learnings.json").read_text())
        detail = pending[0]["diagnosis_details"]
        assert detail["root_cause"] == "the root cause"
        assert detail["confidence"] == 0.6
        assert detail["recommended_fix"] == "log_and_continue"

    def test_does_nothing_when_disabled(self, disabled_agent, kb_dir):
        diagnosis = Diagnosis(
            issue_type="x", root_cause="y", confidence=0.5,
            severity=Severity.LOW,
        )
        disabled_agent.suggest_new_pattern("line", diagnosis, "log_and_continue", True)
        assert not (kb_dir / "pending_learnings.json").exists()
