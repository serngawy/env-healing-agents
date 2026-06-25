"""
Tests for remediation/remediation_agent.py
"""

import pytest
from env_healing_agent.core.event import ActionType, FixStrategy, SequenceStep
from env_healing_agent.remediation.remediation_agent import RemediationAgent


@pytest.fixture
def agent(kb_dir):
    return RemediationAgent(kb_dir=kb_dir, enabled=True, verbose=False, dry_run=False)


@pytest.fixture
def disabled_agent(kb_dir):
    return RemediationAgent(kb_dir=kb_dir, enabled=False, verbose=False)


@pytest.fixture
def dry_run_agent(kb_dir):
    return RemediationAgent(kb_dir=kb_dir, enabled=True, verbose=False, dry_run=True)


# ── ActionType ────────────────────────────────────────────────────────────────

class TestActionType:

    def test_all_values(self):
        assert ActionType.ADVISORY.value == "advisory"
        assert ActionType.CLI_COMMAND.value == "cli_command"
        assert ActionType.CLI_SEQUENCE.value == "cli_sequence"
        assert ActionType.KUBECTL_PATCH.value == "kubectl_patch"

    def test_str_comparison(self):
        assert ActionType.ADVISORY == "advisory"
        assert ActionType.CLI_SEQUENCE == "cli_sequence"


# ── SequenceStep ──────────────────────────────────────────────────────────────

class TestSequenceStep:

    def test_from_dict_command_step(self):
        data = {
            "name": "check-deployment",
            "type": "command",
            "command": ["oc", "get", "deploy", "-n", "{namespace}"],
            "timeout": 15,
            "optional": True,
            "wait_after": 5,
        }
        step = SequenceStep.from_dict(data)
        assert step.name == "check-deployment"
        assert step.type == "command"
        assert step.command == ["oc", "get", "deploy", "-n", "{namespace}"]
        assert step.timeout == 15
        assert step.optional is True
        assert step.wait_after == 5
        assert step.shell is None

    def test_from_dict_shell_step_string(self):
        data = {"name": "cleanup", "type": "shell", "shell": "echo hello"}
        step = SequenceStep.from_dict(data)
        assert step.shell == "echo hello"
        assert step.command == []

    def test_from_dict_shell_step_list(self):
        data = {
            "name": "multi-line",
            "type": "shell",
            "shell": ["echo one", "echo two", "echo three"],
        }
        step = SequenceStep.from_dict(data)
        assert step.shell == "echo one\necho two\necho three"

    def test_from_dict_defaults(self):
        step = SequenceStep.from_dict({"name": "minimal", "type": "command"})
        assert step.timeout == 30
        assert step.optional is False
        assert step.wait_after == 0
        assert step.command == []

    def test_to_dict_command_step(self):
        step = SequenceStep(name="s1", type="command", command=["ls"], timeout=10)
        d = step.to_dict()
        assert d["name"] == "s1"
        assert d["command"] == ["ls"]
        assert "shell" not in d

    def test_to_dict_shell_step(self):
        step = SequenceStep(name="s2", type="shell", shell="echo hi")
        d = step.to_dict()
        assert d["shell"] == "echo hi"
        assert "command" not in d

    def test_round_trip_command(self):
        data = {"name": "step", "type": "command", "command": ["pwd"], "timeout": 5}
        step = SequenceStep.from_dict(data)
        d = step.to_dict()
        step2 = SequenceStep.from_dict(d)
        assert step2.name == step.name
        assert step2.command == step.command
        assert step2.timeout == step.timeout


# ── FixStrategy ───────────────────────────────────────────────────────────────

class TestFixStrategy:

    def _advisory_data(self, **overrides):
        base = {
            "name": "Log and Continue",
            "description": "Log the issue and continue without action",
            "automated": False,
            "action_type": "advisory",
            "parameters": [],
            "action": {"message": "Logged issue: {issue}", "success": True},
        }
        base.update(overrides)
        return base

    def test_from_dict_advisory(self):
        s = FixStrategy.from_dict("log_and_continue", self._advisory_data())
        assert s.key == "log_and_continue"
        assert s.name == "Log and Continue"
        assert s.action_type == ActionType.ADVISORY
        assert s.automated is False
        assert s.parameters == []
        assert s.action["success"] is True

    def test_from_dict_cli_command(self):
        data = self._advisory_data(action_type="cli_command")
        s = FixStrategy.from_dict("my_fix", data)
        assert s.action_type == ActionType.CLI_COMMAND

    def test_from_dict_cli_sequence(self):
        data = self._advisory_data(action_type="cli_sequence")
        s = FixStrategy.from_dict("seq_fix", data)
        assert s.action_type == ActionType.CLI_SEQUENCE

    def test_from_dict_kubectl_patch(self):
        data = self._advisory_data(action_type="kubectl_patch")
        s = FixStrategy.from_dict("patch_fix", data)
        assert s.action_type == ActionType.KUBECTL_PATCH

    def test_from_dict_unknown_action_type_defaults_to_advisory(self):
        data = self._advisory_data(action_type="totally_unknown")
        s = FixStrategy.from_dict("k", data)
        assert s.action_type == ActionType.ADVISORY

    def test_from_dict_missing_name_uses_key(self):
        data = self._advisory_data()
        del data["name"]
        s = FixStrategy.from_dict("fallback_key", data)
        assert s.name == "fallback_key"

    def test_to_dict_round_trip(self):
        data = self._advisory_data()
        s = FixStrategy.from_dict("log_and_continue", data)
        d = s.to_dict()
        s2 = FixStrategy.from_dict("log_and_continue", d)
        assert s2.action_type == s.action_type
        assert s2.name == s.name
        assert s2.description == s.description
        assert s2.action == s.action

    def test_to_dict_action_type_is_string(self):
        s = FixStrategy.from_dict("k", self._advisory_data())
        assert s.to_dict()["action_type"] == "advisory"

    def test_parameters_list(self):
        data = self._advisory_data(parameters=["namespace", "resource_name"])
        s = FixStrategy.from_dict("k", data)
        assert s.parameters == ["namespace", "resource_name"]


# ── RemediationAgent ──────────────────────────────────────────────────────────

class TestRemediationAgentDisabled:

    def test_returns_false_when_disabled(self, disabled_agent):
        success, msg = disabled_agent.remediate({"recommended_fix": "log_and_continue"})
        assert success is False
        assert "disabled" in msg.lower()


class TestRemediationAgentDryRun:

    def test_returns_true_in_dry_run(self, dry_run_agent):
        success, msg = dry_run_agent.remediate({"recommended_fix": "log_and_continue"})
        assert success is True
        assert "DRY RUN" in msg

    def test_dry_run_includes_fix_name(self, dry_run_agent):
        _, msg = dry_run_agent.remediate({"recommended_fix": "log_and_continue"})
        assert "log_and_continue" in msg


class TestRemediationAgentNoStrategy:

    def test_returns_false_for_unknown_fix(self, agent):
        success, msg = agent.remediate({"recommended_fix": "nonexistent_fix"})
        assert success is False
        assert "nonexistent_fix" in msg

    def test_returns_false_when_no_fix_specified(self, agent):
        success, msg = agent.remediate({})
        assert success is False


class TestRemediationAgentAdvisory:

    def test_advisory_fix_succeeds(self, agent):
        success, msg = agent.remediate({"recommended_fix": "log_and_continue"})
        assert success is True

    def test_advisory_returns_message(self, agent):
        _, msg = agent.remediate({"recommended_fix": "log_and_continue"})
        assert isinstance(msg, str)
        assert len(msg) > 0

    def test_advisory_records_stats(self, agent):
        agent.remediate({"recommended_fix": "log_and_continue"})
        stats = agent.get_success_rate("log_and_continue")
        assert stats["successes"] == 1
        assert stats["total_attempts"] == 1

    def test_advisory_failure_flag_tracked(self, agent, kb_dir):
        import json
        # add an advisory strategy that reports success=False
        data = json.loads((kb_dir / "fix_strategies.json").read_text())
        data["fix_strategies"]["fail_advisory"] = {
            "action_type": "advisory",
            "parameters": [],
            "action": {"message": "must intervene", "success": False},
        }
        (kb_dir / "fix_strategies.json").write_text(json.dumps(data))
        a = RemediationAgent(kb_dir=kb_dir, enabled=True, verbose=False)
        success, _ = a.remediate({"recommended_fix": "fail_advisory"})
        assert success is False
        assert a.get_success_rate("fail_advisory")["failures"] == 1


class TestFixStrategiesProperty:

    def test_returns_fix_strategy_objects(self, agent):
        strategies = agent.fix_strategies
        assert all(isinstance(v, FixStrategy) for v in strategies.values())

    def test_known_strategy_present(self, agent):
        assert "log_and_continue" in agent.fix_strategies

    def test_strategy_action_type_enum(self, agent):
        s = agent.fix_strategies["log_and_continue"]
        assert s.action_type == ActionType.ADVISORY
        assert isinstance(s.action_type, ActionType)

    def test_cached_on_second_access(self, agent):
        first = agent.fix_strategies
        second = agent.fix_strategies
        assert first is second
