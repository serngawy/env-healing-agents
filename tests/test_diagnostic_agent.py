"""
Tests for diagnostic/diagnostic_agent.py
"""

import pytest
from env_healing_agent.core.event import Diagnosis, Issue, KnownIssuePattern, LogLine, Severity
from env_healing_agent.diagnostic.diagnostic_agent import DiagnosticAgent


@pytest.fixture
def agent(kb_dir):
    return DiagnosticAgent(kb_dir=kb_dir, enabled=True, verbose=False)


@pytest.fixture
def disabled_agent(kb_dir):
    return DiagnosticAgent(kb_dir=kb_dir, enabled=False, verbose=False)


def _make_issue(issue_type="test_issue", pattern_dict=None, context=None):
    pattern_dict = pattern_dict or {
        "type": issue_type,
        "pattern": "test error occurred",
        "severity": "medium",
        "auto_fix": True,
        "recommended_fix": "log_and_continue",
        "description": "A test issue",
        "symptoms": ["symptom A", "symptom B"],
        "common_causes": ["cause X"],
        "learned_confidence": 0.8,
    }
    return Issue(
        issue_type=issue_type,
        pattern=KnownIssuePattern.from_dict(pattern_dict),
        log_line=LogLine(content="test error occurred", stream_name="test"),
        context=context or {"buffer": ["line1", "line2"]},
        resource_key="default/test-resource",
    )


# ── diagnose() ────────────────────────────────────────────────────────────────

class TestDiagnose:

    def test_returns_none_when_disabled(self, disabled_agent):
        issue = _make_issue()
        assert disabled_agent.diagnose(issue) is None

    def test_returns_diagnosis_when_enabled(self, agent):
        issue = _make_issue()
        result = agent.diagnose(issue)
        assert result is not None
        assert isinstance(result, Diagnosis)

    def test_diagnosis_issue_type_matches(self, agent):
        issue = _make_issue(issue_type="test_issue")
        result = agent.diagnose(issue)
        assert result.issue_type == "test_issue"

    def test_sets_current_diagnosis(self, agent):
        issue = _make_issue()
        agent.diagnose(issue)
        assert agent.current_diagnosis is not None

    def test_uses_ai_fallback_when_no_client(self, agent):
        assert agent._ai_client is None  # no AI env vars set in tests
        issue = _make_issue()
        result = agent.diagnose(issue)
        assert result is not None  # falls back to built-in


# ── _diagnose_builtin() ───────────────────────────────────────────────────────

class TestDiagnoseBuiltin:

    def test_root_cause_from_pattern_description(self, agent):
        issue = _make_issue()
        result = agent._diagnose_builtin(issue)
        assert result.root_cause == "A test issue"

    def test_severity_from_pattern(self, agent):
        issue = _make_issue()
        result = agent._diagnose_builtin(issue)
        assert result.severity == Severity.MEDIUM

    def test_confidence_from_learned_confidence(self, agent):
        issue = _make_issue()
        result = agent._diagnose_builtin(issue)
        assert result.confidence == 0.8

    def test_evidence_from_symptoms(self, agent):
        issue = _make_issue()
        result = agent._diagnose_builtin(issue)
        assert "symptom A" in result.evidence
        assert "symptom B" in result.evidence

    def test_recommended_fix_from_pattern(self, agent):
        issue = _make_issue()
        result = agent._diagnose_builtin(issue)
        assert result.recommended_fix == "log_and_continue"

    def test_recommended_fix_falls_back_when_none(self, agent):
        pattern_dict = {
            "type": "no_fix_issue",
            "pattern": "boom",
            "severity": "low",
            "auto_fix": False,
            "description": "no fix defined",
            "symptoms": [],
            "common_causes": [],
            "learned_confidence": 0.5,
        }
        issue = _make_issue(issue_type="no_fix_issue", pattern_dict=pattern_dict)
        result = agent._diagnose_builtin(issue)
        assert result.recommended_fix == "log_and_continue"

    def test_high_severity_pattern(self, agent):
        pattern_dict = {
            "type": "critical_issue",
            "pattern": "critical failure",
            "severity": "high",
            "auto_fix": True,
            "recommended_fix": "log_and_continue",
            "description": "critical problem",
            "symptoms": ["crash"],
            "common_causes": ["bug"],
            "learned_confidence": 1.0,
        }
        issue = _make_issue(issue_type="critical_issue", pattern_dict=pattern_dict)
        result = agent._diagnose_builtin(issue)
        assert result.severity == Severity.HIGH
        assert result.confidence == 1.0


# ── _apply_learned_confidence() ───────────────────────────────────────────────

class TestApplyLearnedConfidence:

    def test_adjusts_confidence_upward(self, agent):
        # pattern in kb_dir has learned_confidence=1.0 for "high_confidence_issue"
        diagnosis = Diagnosis(
            issue_type="high_confidence_issue",
            root_cause="x",
            confidence=0.5,
            severity=Severity.HIGH,
        )
        result = agent._apply_learned_confidence(diagnosis)
        # delta capped at +0.1, so 0.5 → 0.6
        assert result.confidence == 0.6

    def test_adds_evidence_note_on_adjustment(self, agent):
        diagnosis = Diagnosis(
            issue_type="high_confidence_issue",
            root_cause="x",
            confidence=0.5,
            severity=Severity.HIGH,
        )
        result = agent._apply_learned_confidence(diagnosis)
        assert any("Confidence adjusted" in e for e in result.evidence)

    def test_no_adjustment_for_unknown_issue_type(self, agent):
        diagnosis = Diagnosis(
            issue_type="unknown_type",
            root_cause="x",
            confidence=0.5,
            severity=Severity.LOW,
        )
        result = agent._apply_learned_confidence(diagnosis)
        assert result.confidence == 0.5
        assert result.evidence == []

    def test_confidence_clamped_to_1(self, agent):
        diagnosis = Diagnosis(
            issue_type="high_confidence_issue",
            root_cause="x",
            confidence=0.99,
            severity=Severity.HIGH,
        )
        result = agent._apply_learned_confidence(diagnosis)
        assert result.confidence <= 1.0


# ── _dict_to_diagnosis() ─────────────────────────────────────────────────────

class TestDictToDiagnosis:

    def test_all_fields_mapped(self, agent):
        data = {
            "issue_type": "test_issue",
            "root_cause": "something broke",
            "confidence": 0.75,
            "severity": "high",
            "evidence": ["log line 1"],
            "recommended_fix": "log_and_continue",
            "fix_parameters": {"key": "val"},
        }
        d = agent._dict_to_diagnosis(data)
        assert d.issue_type == "test_issue"
        assert d.root_cause == "something broke"
        assert d.confidence == 0.75
        assert d.severity == Severity.HIGH
        assert d.evidence == ["log line 1"]
        assert d.recommended_fix == "log_and_continue"
        assert d.fix_parameters == {"key": "val"}

    def test_invalid_severity_defaults_to_medium(self, agent):
        data = {"issue_type": "x", "root_cause": "y", "confidence": 0.5,
                "severity": "bogus"}
        d = agent._dict_to_diagnosis(data)
        assert d.severity == Severity.MEDIUM

    def test_missing_fields_use_defaults(self, agent):
        d = agent._dict_to_diagnosis({})
        assert d.issue_type == "unknown"
        assert d.confidence == 0.5
        assert d.evidence == []
        assert d.fix_parameters == {}


# ── get_diagnosis_summary() ───────────────────────────────────────────────────

class TestGetDiagnosisSummary:

    def test_returns_none_before_any_diagnosis(self, agent):
        assert agent.get_diagnosis_summary() is None

    def test_summary_contains_key_fields(self, agent):
        issue = _make_issue()
        agent.diagnose(issue)
        summary = agent.get_diagnosis_summary()
        assert "test_issue" in summary
        assert "A test issue" in summary
        assert "medium" in summary.lower()
        assert "log_and_continue" in summary

    def test_summary_shows_builtin_path(self, agent):
        issue = _make_issue()
        agent.diagnose(issue)
        assert "built-in" in agent.get_diagnosis_summary()


# ── _load_known_patterns() ────────────────────────────────────────────────────

class TestLoadKnownPatterns:

    def test_returns_list_of_known_issue_patterns(self, agent):
        patterns = agent._load_known_patterns()
        assert len(patterns) == 2
        assert all(isinstance(p, KnownIssuePattern) for p in patterns)

    def test_pattern_types_match_kb(self, agent):
        types = {p.type for p in agent._load_known_patterns()}
        assert "test_issue" in types
        assert "high_confidence_issue" in types
