"""
Diagnostic env-healing-agents
===================

Analyzes detected issues to determine root cause and recommended fixes.

Primary path — AI client (Claude or Gemini):
  The agent sends the raw log chunk surrounding the detected issue to the
  configured AI client, which returns a structured diagnosis and any new issue
  patterns it identifies. New patterns are persisted to known_issues.json
  immediately so future runs benefit from them automatically.

  Select the client via the AI_CLIENT environment variable (or --ai-client CLI
  flag). Only one client may be active at runtime.

  Claude (via Vertex AI):
    AI_CLIENT=claude
    ANTHROPIC_VERTEX_PROJECT_ID=<gcp-project>
    CLOUD_ML_REGION=<region>

  Gemini:
    AI_CLIENT=gemini
    GEMINI_API_KEY=<api-key>
    GEMINI_MODEL=<model>   (default: gemini-2.0-flash)

Fallback — pattern-driven built-in:
  When no AI client is configured or available, the agent builds a generic
  diagnosis directly from the KnownIssuePattern metadata (description, severity,
  symptoms, recommended_fix). No per-issue-type Python logic required — adding
  a new issue type only needs an entry in known_issues.json.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..core.base_agent import BaseAgent
from ..core.event import Diagnosis, Issue, KnownIssuePattern, Severity


class DiagnosticAgent(BaseAgent):
    """Analyzes issues to determine root cause and fix strategy."""

    def __init__(self, kb_dir: Path, enabled: bool = True, verbose: bool = False):
        super().__init__("Diagnostic", kb_dir, enabled, verbose)
        self.current_diagnosis: Optional[Diagnosis] = None
        self._ai_client = self._init_ai_client()

    # ── Initialisation ────────────────────────────────────────────────────────

    def _init_ai_client(self):
        """
        Select and initialise the AI diagnostic client.

        Resolution order:
          1. AI_CLIENT env var explicitly set to "claude" or "gemini".
          2. Auto-detect from credentials present in the environment.
          3. Fall back to built-in methods when neither is configured.

        Logs an error and falls back when both sets of credentials are present
        but AI_CLIENT is not set — the operator must make the choice explicit.
        """
        explicit = os.environ.get("AI_CLIENT", "").lower().strip()
        has_claude = bool(
            os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
            and os.environ.get("CLOUD_ML_REGION")
        )
        has_gemini = bool(os.environ.get("GEMINI_API_KEY"))

        if explicit == "claude":
            return self._try_claude()
        if explicit == "gemini":
            return self._try_gemini()

        if has_claude and has_gemini:
            self.log(
                "Both Claude and Gemini credentials are present. "
                "Set AI_CLIENT=claude or AI_CLIENT=gemini to choose one. "
                "Falling back to built-in diagnosis methods.",
                "error",
            )
            return None
        if has_claude:
            return self._try_claude()
        if has_gemini:
            return self._try_gemini()

        self.log(
            "No AI client credentials found — using built-in diagnosis methods. "
            "Set AI_CLIENT=claude (with Vertex AI vars) or AI_CLIENT=gemini (with GEMINI_API_KEY).",
            "info",
        )
        return None

    def _try_claude(self):
        """Initialise a ClaudeClient; return None on failure."""
        project_id = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
        region = os.environ.get("CLOUD_ML_REGION")
        if not project_id or not region:
            self.log(
                "AI_CLIENT=claude but ANTHROPIC_VERTEX_PROJECT_ID or CLOUD_ML_REGION not set "
                "— using built-in diagnosis methods",
                "error",
            )
            return None
        try:
            from .claude_client import ClaudeClient
            client = ClaudeClient()
            self.log(f"Claude diagnostic client ready (project={project_id}, region={region})", "info")
            return client
        except ImportError:
            self.log(
                "anthropic package not installed — using built-in diagnosis methods "
                "(run: pip install anthropic)",
                "warning",
            )
            return None
        except Exception as e:
            self.log(f"Failed to initialise Claude client: {e} — using built-in methods", "warning")
            return None

    def _try_gemini(self):
        """Initialise a GeminiClient; return None on failure."""
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            self.log(
                "AI_CLIENT=gemini but GEMINI_API_KEY not set "
                "— using built-in diagnosis methods",
                "error",
            )
            return None
        try:
            from .gemini_client import GeminiClient
            model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
            client = GeminiClient(api_key=api_key, model=model)
            self.log(f"Gemini diagnostic client ready (model={model})", "info")
            return client
        except ImportError:
            self.log(
                "google-generativeai package not installed — using built-in diagnosis methods "
                "(run: pip install google-generativeai)",
                "warning",
            )
            return None
        except Exception as e:
            self.log(f"Failed to initialise Gemini client: {e} — using built-in methods", "warning")
            return None

    # ── Public API ────────────────────────────────────────────────────────────

    def diagnose(self, issue: Issue) -> Optional[Diagnosis]:
        """
        Diagnose an issue and return a recommended fix.

        Tries the AI client path first. Falls back to pattern-driven built-in
        diagnosis when the AI client is unavailable or returns an unusable response.

        Returns a Diagnosis with issue_type, root_cause, severity, confidence,
        evidence, recommended_fix, and fix_parameters.
        """
        if not self.enabled:
            return None

        self.log(f"Diagnosing: {issue.issue_type}", "info")

        if self._ai_client is not None:
            diagnosis = self._diagnose_with_ai(issue)
            if diagnosis is not None:
                diagnosis = self._apply_learned_confidence(diagnosis)
                self.current_diagnosis = diagnosis
                return diagnosis
            self.log("AI diagnosis failed — falling back to built-in methods", "warning")

        diagnosis = self._diagnose_builtin(issue)
        diagnosis = self._apply_learned_confidence(diagnosis)
        self.current_diagnosis = diagnosis
        return diagnosis

    # ── AI primary path ───────────────────────────────────────────────────────

    def _diagnose_with_ai(self, issue: Issue) -> Optional[Diagnosis]:
        """Call the active AI client with the log chunk and persist any new patterns."""
        log_chunk: List[str] = [issue.log_line.content] + issue.context.get("buffer", [])
        known_patterns: List[Dict] = [p.to_dict() for p in self._load_known_patterns()]
        fix_strategies: Dict = self._load_knowledge("fix_strategies.json").get("fix_strategies", {})

        try:
            result, new_patterns = self._ai_client.diagnose(
                issue_type=issue.issue_type,
                log_chunk=log_chunk,
                known_patterns=known_patterns,
                fix_strategies=fix_strategies,
            )
        except Exception as e:
            self.log(f"AI client error: {e}", "error")
            return None

        if not result:
            return None

        if new_patterns:
            self._persist_new_patterns(new_patterns)

        new_strategies = result.pop("new_fix_strategies", None)
        if new_strategies:
            self._persist_new_fix_strategies(new_strategies)

        return self._dict_to_diagnosis(result)

    def _persist_new_patterns(self, new_patterns: List[Dict]) -> None:
        """Merge AI-discovered patterns into known_issues.json and sync to ConfigMap."""
        try:
            data = json.loads(json.dumps(self.known_issues))  # deep copy via JSON round-trip
            existing_types = {p.get("type") for p in data.get("patterns", [])}
            added: List[str] = []

            for pattern in new_patterns:
                ptype = pattern.get("type", "").strip()
                if not ptype or ptype in existing_types:
                    continue
                pattern["learned_confidence"] = pattern.get("learned_confidence", 0.5)
                pattern["last_adjusted"] = datetime.now().isoformat()
                pattern["adjustment_reason"] = "discovered by AI diagnostic agent at runtime"
                data.setdefault("patterns", []).append(pattern)
                existing_types.add(ptype)
                added.append(ptype)

            if not added:
                return

            self._save_knowledge("known_issues.json", data)
            self._known_issues = None
            self.log(
                f"Persisted {len(added)} new pattern(s) to known_issues.json: {added}",
                "success",
            )
            cm = os.environ.get("KNOWN_ISSUES_CONFIGMAP", "")
            self._sync_to_configmap(cm, data)
        except OSError as e:
            self.log(f"Cannot write to known_issues.json ({e}) — new patterns not persisted", "warning")
        except Exception as e:
            self.log(f"Unexpected error persisting patterns: {e}", "warning")

    def _persist_new_fix_strategies(self, new_strategies: Dict) -> None:
        """Merge AI-discovered fix strategies into fix_strategies.json and sync to ConfigMap."""
        if not new_strategies:
            return
        try:
            data = self._load_knowledge("fix_strategies.json")
            existing = data.setdefault("fix_strategies", {})
            added = [k for k in new_strategies if k not in existing]
            if not added:
                return
            existing.update({k: new_strategies[k] for k in added})
            self._save_knowledge("fix_strategies.json", data)
            self.log(
                f"Persisted {len(added)} new fix strategy/strategies to fix_strategies.json: {added}",
                "success",
            )
            cm = os.environ.get("FIX_STRATEGIES_CONFIGMAP", "")
            self._sync_to_configmap(cm, data)
        except OSError as e:
            self.log(f"Cannot write to fix_strategies.json ({e}) — strategies not persisted", "warning")
        except Exception as e:
            self.log(f"Unexpected error persisting fix strategies: {e}", "warning")

    # ── Built-in fallback ─────────────────────────────────────────────────────

    def _diagnose_builtin(self, issue: Issue) -> Diagnosis:
        """Build a generic diagnosis directly from the KnownIssuePattern metadata."""
        pattern = issue.pattern
        recommended_fix = pattern.recommended_fix or "log_and_continue"
        self.log(f"Diagnosis complete. Confidence: {pattern.learned_confidence}", "info")
        return Diagnosis(
            issue_type=pattern.type,
            root_cause=pattern.description,
            severity=pattern.severity,
            confidence=pattern.learned_confidence,
            evidence=list(pattern.symptoms),
            recommended_fix=recommended_fix,
            fix_parameters={},
        )

    # ── Confidence learning ───────────────────────────────────────────────────

    def _apply_learned_confidence(self, diagnosis: Diagnosis) -> Diagnosis:
        for pattern in self.known_issues.get("patterns", []):
            if pattern.get("type") == diagnosis.issue_type and "learned_confidence" in pattern:
                learned = pattern["learned_confidence"]
                original = diagnosis.confidence
                delta = max(-0.1, min(0.1, learned - original))
                adjusted = max(0.0, min(1.0, round(original + delta, 2)))
                if adjusted != original:
                    diagnosis.confidence = adjusted
                    diagnosis.evidence.append(
                        f"Confidence adjusted {original} -> {adjusted} "
                        f"(learned from {pattern.get('adjustment_reason', 'historical outcomes')})"
                    )
                break
        return diagnosis

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _load_known_patterns(self) -> List[KnownIssuePattern]:
        """Load all known patterns as typed KnownIssuePattern objects."""
        return [KnownIssuePattern.from_dict(p) for p in self.known_issues.get("patterns", [])]

    def _dict_to_diagnosis(self, data: Dict) -> Diagnosis:
        """Convert an AI client result dict to a Diagnosis dataclass."""
        try:
            severity = Severity(data.get("severity", "medium"))
        except ValueError:
            severity = Severity.MEDIUM
        return Diagnosis(
            issue_type=data.get("issue_type", "unknown"),
            root_cause=data.get("root_cause", "Unknown"),
            confidence=float(data.get("confidence", 0.5)),
            severity=severity,
            evidence=data.get("evidence", []),
            recommended_fix=data.get("recommended_fix"),
            fix_parameters=data.get("fix_parameters", {}),
        )

    # ── Reporting ─────────────────────────────────────────────────────────────

    def get_diagnosis_summary(self) -> Optional[str]:
        if not self.current_diagnosis:
            return None
        d = self.current_diagnosis
        evidence_lines = "\n".join(f"    - {e}" for e in d.evidence)
        return (
            f"Diagnosis Summary:\n"
            f"  Issue: {d.issue_type}\n"
            f"  Root Cause: {d.root_cause}\n"
            f"  Severity: {d.severity.value}\n"
            f"  Confidence: {d.confidence * 100:.0f}%\n"
            f"  Recommended Fix: {d.recommended_fix}\n"
            f"  Evidence:\n{evidence_lines}\n"
            f"  Path: {type(self._ai_client).__name__ if self._ai_client else 'built-in'}\n"
        )
