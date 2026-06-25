# env-healing-agents — Developer Reference

- [Log Streams](#log-streams)
- [Runner Adapters](#runner-adapters)
- [CLI Usage](#cli-usage)
- [Python API](#python-api)
- [Data Model](#data-model)
- [Testing](#testing)

---

## Log Streams

All streams implement `BaseLogStream` and the context manager protocol (`with stream:`). They yield `LogLine` objects carrying content, timestamp, stream name, and stream-specific metadata. Any number of streams can run simultaneously — each in its own daemon thread, all multiplexed into a single queue.

| Class | Source | In-Pod |
|---|---|---|
| `StdoutStream` | Subprocess stdout + stderr | Works as-is |
| `FileTailStream` | File on disk | Requires a `hostPath` or `emptyDir` volume mount |
| `KubernetesLogStream` | Kubernetes pod logs | SDK mode auto-detected — no kubectl needed |
| `PipeStream` | `sys.stdin` or any file object | Works as-is |
| `CloudWatchStream` | AWS CloudWatch Logs | Works with Secret env vars or IRSA |
| `JournaldStream` | systemd journald | Requires `journal_path` + `hostPath` volume mount |

### KubernetesLogStream — dual mode

| Mode | When | How |
|---|---|---|
| **SDK** (default inside a pod) | `KUBERNETES_SERVICE_HOST` is set | Uses the `kubernetes` Python library; authenticates via the mounted service account token — no `kubectl` binary needed |
| **Subprocess** (default outside a pod) | `KUBERNETES_SERVICE_HOST` not set | Runs `kubectl logs -f` as a subprocess |

Override explicitly with `use_sdk=True` or `use_sdk=False`. When `label_selector` is used in SDK mode, every matching pod is streamed concurrently.

#### Multi-namespace support

The `namespace` parameter accepts a single namespace string, a list of strings, or `"*"` to watch every namespace. SDK mode calls `list_pod_for_all_namespaces()` for the `"*"` case; subprocess mode spawns one `kubectl logs -f` thread per namespace. Overlapping or duplicate entries are automatically de-duplicated.

```python
# Single namespace (default)
KubernetesLogStream(label_selector="app=worker", namespace="default")

# Two specific namespaces — streams merged into one queue
KubernetesLogStream(label_selector="app=worker", namespace=["ns-a", "ns-b"])

# All namespaces — requires ClusterRole with list/watch on pods cluster-wide
KubernetesLogStream(label_selector="app=worker", namespace="*")
```

From the CLI, pass `--k8s-namespace` once per namespace or use `"*"` for all:

```bash
# Two namespaces
python -m env_healing_agent.cli generic sleep infinity \
    --k8s-label app=my-service \
    --k8s-namespace default \
    --k8s-namespace kube-system

# All namespaces
python -m env_healing_agent.cli generic sleep infinity \
    --k8s-label app=my-service \
    --k8s-namespace "*"
```

### JournaldStream — in-pod requirements

journald runs on the **host**, not inside a container. To use `JournaldStream` from a pod:

1. Mount the host journal directory as a read-only `hostPath` volume:
   ```yaml
   volumes:
     - name: host-journal
       hostPath:
         path: /var/log/journal
         type: DirectoryOrCreate
   volumeMounts:
     - name: host-journal
       mountPath: /host/var/log/journal
       readOnly: true
   ```
2. Set `hostPID: true` on the pod spec so `journalctl` can resolve UIDs.
3. Pass the mount path: `--journald-unit kubelet --journald-path /host/var/log/journal`

### In-pod stream requirements

| Stream | Works in pod? | What's needed |
|---|---|---|
| `KubernetesLogStream` | Yes — auto SDK | ServiceAccount with `pods/log` RBAC (in `rbac.yaml`) |
| `FileTailStream` | Yes | `hostPath` or `emptyDir` volume mounted at the tailed path |
| `CloudWatchStream` | Yes | AWS credentials via Secret env vars or IRSA |
| `StdoutStream` | Yes | Command binary installed in the image |
| `PipeStream` | Yes | No requirements |
| `JournaldStream` | Yes, with config | `journal_path` set + `hostPath` volume + `hostPID: true` |

### Example: combining streams

```python
from env_healing_agent.core.pipeline import AgentPipeline
from env_healing_agent.frameworks import AnsibleFramework
from env_healing_agent.log_streams import KubernetesLogStream, CloudWatchStream
from pathlib import Path

pipeline = AgentPipeline(
    framework=AnsibleFramework("playbooks/provision_cluster.yml"),
    kb_dir=Path("knowledge_base"),
    extra_streams=[
        # Watch controller logs while the playbook runs
        KubernetesLogStream(label_selector="app=capa-controller", namespace="capa-system"),
        # Also watch the cloud provider's control-plane log group
        CloudWatchStream(log_group="/aws/eks/my-cluster/cluster", region="us-east-1"),
    ],
)
pipeline.run()
```

---

## Runner Adapters

A runner adapter wraps the process being monitored and provides:
- `get_log_streams()` — the log sources for this run
- `parse_context_marker(line)` — extract structured context from process-specific output markers

| Class | Wraps | Context parsing |
|---|---|---|
| `AnsibleFramework` | `ansible-playbook` | `#AGENT_CONTEXT: key=value` task markers |
| `PytestFramework` | `pytest` | `PASSED`/`FAILED`/`ERROR` result lines |
| `ShellFramework` | `bash`/`sh` scripts | None by default (override to add) |
| `GenericSubprocessFramework` | Any command | None by default |
| `PipeFramework` | `sys.stdin` or file | None by default |

### Adding a custom adapter

```python
from env_healing_agent.frameworks.base_framework import BaseTestFramework
from env_healing_agent.log_streams import StdoutStream
from typing import Dict, List, Optional

class TerraformFramework(BaseTestFramework):
    def __init__(self, working_dir: str):
        self.working_dir = working_dir

    @property
    def name(self) -> str:
        return "terraform"

    def get_log_streams(self) -> List:
        return [StdoutStream(
            command=["terraform", "apply", "-auto-approve"],
            name="terraform:stdout",
            cwd=self.working_dir,
        )]

    def parse_context_marker(self, line: str) -> Optional[Dict]:
        # Extract resource names from Terraform output lines
        if line.startswith("aws_"):
            parts = line.split(":")
            return {"resource": parts[0].strip()} if parts else None
        return None
```

---

## CLI Usage

```
python -m env_healing_agent.cli <runner> [runner-args] [common-flags]
```

### Common flags

| Flag | Description |
|---|---|
| `--dry-run` | Detect and diagnose issues but do not execute fixes |
| `-v` / `--verbose` | Verbose agent logging |
| `--confidence FLOAT` | Minimum confidence threshold (default: 0.7) |
| `--no-echo` | Suppress echoing log lines to stdout |
| `--report` | Print a JSON report when the run finishes |
| `--kb-dir PATH` | Path to knowledge base directory |

### Extra log stream flags

| Flag | Description |
|---|---|
| `--k8s-pod NAME` | Also stream logs from this Kubernetes pod |
| `--k8s-namespace NS` | Namespace to watch — **repeatable** (one per namespace). Use `"*"` for all namespaces. Default: `default` |
| `--k8s-label SELECTOR` | Stream logs from pods matching this label selector |
| `--k8s-cmd CMD` | kubectl binary for subprocess mode (ignored in SDK mode) |
| `--tail-file PATH` | Tail an additional log file (repeatable) |
| `--journald-unit UNIT` | Stream journald logs for this unit (repeatable) |
| `--journald-path PATH` | Host journal directory mounted into the pod |
| `--cloudwatch-log-group GROUP` | AWS CloudWatch Logs group name to stream |
| `--cloudwatch-region REGION` | AWS region for CloudWatch |
| `--cloudwatch-filter PATTERN` | CloudWatch filter pattern (default: all events) |
| `--cloudwatch-poll SECS` | CloudWatch poll interval in seconds (default: 5) |

### Examples

```bash
# Monitor an Ansible provisioning playbook
python -m env_healing_agent.cli ansible playbooks/provision_cluster.yml \
    -e region=us-east-1 -e cluster_name=my-cluster --report

# Dry-run — detect and diagnose without applying any fixes
python -m env_healing_agent.cli ansible playbooks/deprovision.yml --dry-run

# Monitor a shell script + tail an application log at the same time
python -m env_healing_agent.cli shell scripts/deploy.sh \
    --tail-file /var/log/app/deploy.log --verbose

# Wrap any command (e.g. a Go binary that manages infrastructure)
python -m env_healing_agent.cli generic ./my-operator --verbose

# Feed log output from another process via pipe
some-process | python -m env_healing_agent.cli pipe

# Replay a captured log file for offline analysis
python -m env_healing_agent.cli pipe < captured.log

# Run a playbook while also watching Kubernetes controller logs
python -m env_healing_agent.cli ansible playbooks/install.yml \
    --k8s-label app=my-controller --k8s-namespace operators --report

# Watch host-level systemd units (kubelet, crio) from inside a pod
python -m env_healing_agent.cli generic sleep infinity \
    --journald-unit kubelet \
    --journald-unit crio \
    --journald-path /host/var/log/journal \
    --verbose

# Monitor a CloudWatch log group while running a provisioning playbook
python -m env_healing_agent.cli ansible playbooks/provision.yml \
    --cloudwatch-log-group /aws/eks/my-cluster/cluster \
    --cloudwatch-region us-east-1 \
    --cloudwatch-filter "ERROR" \
    --report
```

---

## Python API

### Minimal usage

```python
from env_healing_agent.core.pipeline import AgentPipeline
from env_healing_agent.frameworks import AnsibleFramework
from pathlib import Path

pipeline = AgentPipeline(
    framework=AnsibleFramework("playbooks/provision_cluster.yml"),
    kb_dir=Path("knowledge_base"),
)
pipeline.run()
report = pipeline.get_report()
```

### `AgentPipeline` parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `framework` | `BaseTestFramework` | required | Runner adapter wrapping the monitored process |
| `kb_dir` | `Path` | required | Knowledge base directory |
| `enabled` | `bool` | `True` | Enable issue detection and remediation |
| `verbose` | `bool` | `False` | Verbose agent logging |
| `dry_run` | `bool` | `False` | Detect/diagnose only — no fixes executed |
| `confidence_threshold` | `float` | `0.7` | Minimum diagnosis confidence to trigger remediation |
| `echo` | `bool` | `True` | Print log lines to stdout as they are processed |
| `extra_streams` | `list` | `[]` | Additional `BaseLogStream` instances to multiplex |

### Run report

```python
report = pipeline.get_report()
# {
#   "framework": "ansible",
#   "timestamp": "2026-04-28T12:00:00",
#   "dry_run": false,
#   "issues_detected": 3,
#   "interventions": 2,
#   "tracked_issues": {
#     "rosanetwork_stuck_deletion:ns/cluster-a": {"state": "resolved", "attempts": 1}
#   },
#   "learning_summary": {"session_outcomes": 2, "pending_reviews": 0},
#   "fix_success_rates": {"retry_cloudformation_delete": {"successes": 1, "failures": 0}}
# }
```

### Embedding in an existing process

```python
from env_healing_agent.core.pipeline import AgentPipeline
from env_healing_agent.frameworks.generic_framework import PipeFramework
import io

# Feed any captured log output for analysis
pipeline = AgentPipeline(
    framework=PipeFramework(source=io.StringIO(log_output)),
    kb_dir=Path("knowledge_base"),
    enabled=True,
    echo=False,
)
pipeline.run()
report = pipeline.get_report()
```

---

## Data Model

All dataclasses live in `core/event.py` and are exported from `core/__init__.py`.

### `Severity`

```python
class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
```

### `KnownIssuePattern`

Represents one entry in `known_issues.json`. Produced by the monitoring agent when a log line matches a pattern; consumed by the diagnostic agent.

| Field | Type | Description |
|---|---|---|
| `type` | `str` | Unique issue identifier |
| `pattern` | `str` | Python regex (case-insensitive) |
| `severity` | `Severity` | |
| `auto_fix` | `bool` | Whether to attempt automated remediation |
| `description` | `str` | Short human-readable description |
| `symptoms` | `List[str]` | Observable symptoms — used as evidence in diagnoses |
| `common_causes` | `List[str]` | Common root causes |
| `recommended_fix` | `Optional[str]` | Fix strategy key; defaults to `log_and_continue` |
| `learned_confidence` | `float` | Confidence score adjusted by the learning agent (0.0–1.0) |
| `last_adjusted` | `Optional[str]` | ISO timestamp of last confidence adjustment |
| `adjustment_reason` | `Optional[str]` | Reason the learning agent last adjusted confidence |

Key methods: `matches(text) -> bool`, `to_dict()`, `from_dict(data)`.

### `Diagnosis`

Produced by the diagnostic agent after analysing an `Issue`.

| Field | Type | Description |
|---|---|---|
| `issue_type` | `str` | Matches `KnownIssuePattern.type` |
| `root_cause` | `str` | Human-readable root cause |
| `confidence` | `float` | 0.0–1.0; must exceed `confidence_threshold` to trigger remediation |
| `severity` | `Severity` | |
| `evidence` | `List[str]` | Log lines or symptoms supporting the diagnosis |
| `recommended_fix` | `Optional[str]` | Key into `fix_strategies.json` |
| `fix_parameters` | `Dict[str, Any]` | `{param}` values substituted into fix strategy commands |

Key method: `to_dict()` — converts to a plain dict for passing to `RemediationAgent.remediate()`.

### `RemediationOutcome`

Persisted record of every remediation attempt, appended to `remediation_outcomes.json` and read by the learning agent.

| Field | Type | Description |
|---|---|---|
| `issue_type` | `str` | |
| `recommended_fix` | `str` | Fix strategy key that was applied |
| `success` | `bool` | Whether the fix succeeded |
| `confidence_used` | `float` | Diagnosis confidence at the time of remediation |
| `root_cause` | `str` | |
| `resource_key` | `str` | `namespace/name` of the affected resource |
| `details` | `str` | Extra notes |
| `timestamp` | `str` | ISO timestamp (auto-set on creation) |

Key methods: `to_dict()`, `from_dict(data)`.

### `ActionType`

```python
class ActionType(str, Enum):
    ADVISORY = "advisory"
    CLI_COMMAND = "cli_command"
    CLI_SEQUENCE = "cli_sequence"
    KUBECTL_PATCH = "kubectl_patch"
```

Typed representation of the `action_type` field in `fix_strategies.json`. Because it inherits `str`, `ActionType.ADVISORY == "advisory"` is `True`.

### `SequenceStep`

One step within a `cli_sequence` fix action.

| Field | Type | Description |
|---|---|---|
| `name` | `str` | Human-readable step label |
| `type` | `str` | `"command"` or `"shell"` |
| `command` | `List[str]` | Argv list (used when `type == "command"`) |
| `shell` | `Optional[str]` | Shell script body (used when `type == "shell"`) |
| `timeout` | `int` | Seconds before the step is killed (default 30) |
| `optional` | `bool` | If `True`, sequence continues even if this step fails |
| `wait_after` | `int` | Seconds to sleep after the step completes |

`from_dict()` accepts `shell` as either a plain string or a list of strings (joined with `\n` at parse time so executors never handle the duality).

Key methods: `to_dict()`, `from_dict(data)`.

### `FixStrategy`

One entry from `fix_strategies.json`, returned by `RemediationAgent.fix_strategies`.

| Field | Type | Description |
|---|---|---|
| `key` | `str` | Dict key in `fix_strategies.json` (e.g. `log_and_continue`) |
| `name` | `str` | Human-readable name |
| `description` | `str` | What the fix does |
| `automated` | `bool` | Whether the fix runs without human confirmation |
| `action_type` | `ActionType` | Determines which executor is used |
| `parameters` | `List[str]` | Names of `{param}` placeholders expected in `fix_parameters` |
| `action` | `Dict[str, Any]` | Executor-specific config (message, command, steps, patch, …) |

Key methods: `to_dict()`, `from_dict(key, data)`. Unknown `action_type` values fall back to `ActionType.ADVISORY`.

### Custom executor example

```python
from env_healing_agent.core.event import ActionType, FixStrategy
from env_healing_agent.remediation.remediation_agent import ActionExecutor
from typing import Tuple

class PagerDutyExecutor(ActionExecutor):
    def execute(self) -> Tuple[bool, str]:
        routing_key = self.strategy.action.get("routing_key", "")
        summary = self.sub(self.strategy.action.get("summary", self.strategy.description))
        # ... call PagerDuty API ...
        return True, f"Incident created: {summary}"

remediation_agent.register_executor("pagerduty", PagerDutyExecutor)
```

Then in `fix_strategies.json`:
```json
"page_oncall": {
  "action_type": "pagerduty",
  "parameters": [],
  "action": {"routing_key": "abc123", "summary": "Critical issue detected"}
}
```

---

## Testing

```bash
# Run the full test suite
make test

# Or directly with pytest
python -m pytest tests/ -v
```

The test suite lives in `tests/` and covers all four agent layers:

| File | Covers |
|---|---|
| `tests/test_event.py` | `KnownIssuePattern`, `Diagnosis`, `RemediationOutcome` dataclasses |
| `tests/test_diagnostic_agent.py` | `DiagnosticAgent` — built-in path, AI fallback, confidence adjustment, pattern loading |
| `tests/test_learning_agent.py` | `LearningAgent` — outcome recording, confidence calculations, pattern suggestions |
| `tests/test_remediation_agent.py` | `ActionType`, `SequenceStep`, `FixStrategy` dataclasses; `RemediationAgent` dispatch, dry-run, stats |

### How tests import the package

The project directory is named `env-healing-agents` (hyphens are not valid Python identifiers). A `conftest.py` at the project root registers the directory as `env_healing_agent` in `sys.modules` so all relative imports resolve correctly during test runs. Test imports use `from env_healing_agent.core.event import ...` etc.

### Fixtures

`tests/conftest.py` provides a `kb_dir` fixture (`tmp_path` with valid `known_issues.json`, `fix_strategies.json`, and `remediation_outcomes.json`) used by all agent tests. Individual tests that need a different knowledge base write directly to the `kb_dir` path before constructing the agent.
