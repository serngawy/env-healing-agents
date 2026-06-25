"""
Shared fixtures for the env-healing-agents test suite.
"""

import json
import pytest
from pathlib import Path


@pytest.fixture
def kb_dir(tmp_path):
    """Minimal knowledge base directory with valid JSON files."""
    known_issues = {
        "version": "1.0.0",
        "patterns": [
            {
                "type": "test_issue",
                "pattern": "test error occurred",
                "severity": "medium",
                "auto_fix": True,
                "recommended_fix": "log_and_continue",
                "description": "A test issue",
                "symptoms": ["symptom A", "symptom B"],
                "common_causes": ["cause X"],
                "learned_confidence": 0.8,
            },
            {
                "type": "high_confidence_issue",
                "pattern": "critical failure",
                "severity": "high",
                "auto_fix": True,
                "recommended_fix": "log_and_continue",
                "description": "A high confidence issue",
                "symptoms": ["crash"],
                "common_causes": ["bug"],
                "learned_confidence": 1.0,
                "last_adjusted": "2026-01-01T00:00:00",
                "adjustment_reason": "3 consecutive successes",
            },
        ],
    }
    fix_strategies = {
        "fix_strategies": {
            "log_and_continue": {
                "action_type": "advisory",
                "parameters": [],
                "action": {"message": "logged", "success": True},
            }
        }
    }
    (tmp_path / "known_issues.json").write_text(json.dumps(known_issues))
    (tmp_path / "fix_strategies.json").write_text(json.dumps(fix_strategies))
    (tmp_path / "remediation_outcomes.json").write_text("[]")
    return tmp_path
