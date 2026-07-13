"""
Claude Diagnostic Client
========================

Sends a chunk of log lines to Claude via Google Cloud Vertex AI and returns:
  - A structured diagnosis for the detected issue
  - Any new issue patterns Claude identifies that are absent from known_issues.json

Authentication: uses Google Cloud Application Default Credentials (ADC).
Required environment variables:
  ANTHROPIC_VERTEX_PROJECT_ID  — GCP project that has Vertex AI / Claude enabled
  CLOUD_ML_REGION              — GCP region (e.g. us-east5)
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Deferred import — the caller checks for ImportError so the rest of the
# package still loads even if the `anthropic` package is not installed.
from anthropic import AnthropicVertex

_DEFAULT_SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.txt"

# Lines of context kept before and after each error/failure line.
_CONTEXT_LINES = 10

# Regex that identifies error/failure lines (case-insensitive).
_ERROR_RE = re.compile(r"\b(error|fail(?:ed|ing)?|fatal|exception|traceback)\b", re.IGNORECASE)

# Fallback: tail this many lines when no error lines are found.
_FALLBACK_LINES = 30


def _extract_error_windows(lines: List[str], context: int = _CONTEXT_LINES) -> str:
    """
    Return a formatted string containing only the error/failure lines together
    with `context` lines before and after each one.

    Overlapping or adjacent windows are merged so each line appears at most once.
    Windows are separated by '--- window N ---' markers to help Claude distinguish
    discontinuous sections.

    Falls back to the last _FALLBACK_LINES lines when no error lines are found.
    """
    if not lines:
        return "(no log lines available)"

    n = len(lines)
    error_indices = [i for i, line in enumerate(lines) if _ERROR_RE.search(line)]

    if not error_indices:
        tail = lines[-_FALLBACK_LINES:]
        return "\n".join(tail)

    # Build merged index ranges, collapsing overlaps.
    ranges: List[tuple] = []
    for idx in error_indices:
        start = max(0, idx - context)
        end = min(n - 1, idx + context)
        if ranges and start <= ranges[-1][1] + 1:
            # Extend the previous range instead of creating a new one.
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))

    sections: List[str] = []
    for window_num, (start, end) in enumerate(ranges, start=1):
        header = f"--- window {window_num} (lines {start + 1}–{end + 1}) ---"
        body = "\n".join(lines[start : end + 1])
        sections.append(f"{header}\n{body}")

    return "\n\n".join(sections)


class ClaudeClient:
    """Thin wrapper around AnthropicVertex for diagnostic use."""

    def __init__(self, model: str = "claude-sonnet-4-6", system_prompt: Optional[str] = None):
        project_id = os.environ["ANTHROPIC_VERTEX_PROJECT_ID"]
        region = os.environ["CLOUD_ML_REGION"]
        self._client = AnthropicVertex(project_id=project_id, region=region)
        self._model = model
        self._system_prompt = system_prompt or self._load_system_prompt()

    def _load_system_prompt(self) -> str:
        prompt_path = Path(os.environ.get("CLAUDE_SYSTEM_PROMPT_PATH", _DEFAULT_SYSTEM_PROMPT_PATH))
        return prompt_path.read_text(encoding="utf-8")

    def diagnose(
        self,
        issue_type: str,
        log_chunk: List[str],
        known_patterns: List[Dict],
        fix_strategies: Dict,
    ) -> Tuple[Optional[Dict], List[Dict], Dict]:
        """
        Ask Claude to diagnose an issue from a log chunk.

        Only error/failure lines (and 10 lines of context around each) are sent
        to Claude. Overlapping windows are merged. When no error lines are found
        the last 30 lines are sent as a fallback.

        Parameters
        ----------
        issue_type
            The pattern type matched by the monitoring agent.
        log_chunk
            Full sliding-window buffer from the monitoring agent.
        known_patterns
            Current entries from known_issues.json — used for deduplication.
        fix_strategies
            Dict from fix_strategies.json — keys and descriptions sent to Claude
            so it can make an informed choice.

        Returns
        -------
        diagnosis
            Structured dict ready for the remediation agent, or None on failure.
        new_patterns
            New issue patterns Claude identified; empty list when none found.
        """
        log_text = _extract_error_windows(log_chunk)

        existing_summary = [
            {
                "type": p.get("type"),
                "description": (p.get("description") or "")[:100],
            }
            for p in known_patterns
        ]

        fix_summary = {
            key: strat.get("description", "")
            for key, strat in fix_strategies.items()
        }

        payload = {
            "issue_type": issue_type,
            "log_chunk": log_text,
            "existing_patterns": existing_summary,
            "available_fix_strategies": fix_summary,
        }

        response = self._client.messages.create(
            model=self._model,
            max_tokens=4096,
            system=[{
                "type": "text",
                "text": self._system_prompt,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": json.dumps(payload, indent=2)}],
        )

        # Find the first text block — skip thinking blocks or other block types.
        raw = ""
        for block in response.content:
            text = getattr(block, "text", None)
            if text:
                raw = text.strip()
                break

        if not raw:
            raise ValueError(
                f"Claude returned no text content (stop_reason={response.stop_reason}, "
                f"blocks={[type(b).__name__ for b in response.content]})"
            )

        # Strip markdown code fences that Claude sometimes wraps around JSON.
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        data = json.loads(raw)

        diagnosis = data.get("diagnosis")
        new_patterns = data.get("new_patterns") or []
        new_fix_strategies = data.get("new_fix_strategies") or {}
        return diagnosis, new_patterns, new_fix_strategies
