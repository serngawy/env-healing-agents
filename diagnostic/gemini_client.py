"""
Gemini Diagnostic Client
========================

Sends a chunk of log lines to the Gemini API and returns:
  - A structured diagnosis for the detected issue
  - Any new issue patterns Gemini identifies that are absent from known_issues.json

Authentication: API key via GEMINI_API_KEY environment variable.
Required environment variables:
  GEMINI_API_KEY  — Gemini API key
  GEMINI_MODEL    — model name (default: gemini-2.0-flash)
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Deferred import — the caller checks for ImportError so the rest of the
# package still loads even if the `google-generativeai` package is not installed.
import google.generativeai as genai

_DEFAULT_SYSTEM_PROMPT_PATH = Path(__file__).parent / "system_prompt.txt"

_CONTEXT_LINES = 10
_ERROR_RE = re.compile(r"\b(error|fail(?:ed|ing)?|fatal|exception|traceback)\b", re.IGNORECASE)
_FALLBACK_LINES = 30


def _extract_error_windows(lines: List[str], context: int = _CONTEXT_LINES) -> str:
    if not lines:
        return "(no log lines available)"

    n = len(lines)
    error_indices = [i for i, line in enumerate(lines) if _ERROR_RE.search(line)]

    if not error_indices:
        return "\n".join(lines[-_FALLBACK_LINES:])

    ranges: List[tuple] = []
    for idx in error_indices:
        start = max(0, idx - context)
        end = min(n - 1, idx + context)
        if ranges and start <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], end))
        else:
            ranges.append((start, end))

    sections: List[str] = []
    for window_num, (start, end) in enumerate(ranges, start=1):
        header = f"--- window {window_num} (lines {start + 1}–{end + 1}) ---"
        body = "\n".join(lines[start : end + 1])
        sections.append(f"{header}\n{body}")

    return "\n\n".join(sections)


class GeminiClient:
    """Thin wrapper around the Gemini generative AI SDK for diagnostic use."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ):
        resolved_key = api_key or os.environ["GEMINI_API_KEY"]
        genai.configure(api_key=resolved_key)
        self._model_name = model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
        self._system_prompt = system_prompt or self._load_system_prompt()
        self._model = genai.GenerativeModel(
            model_name=self._model_name,
            system_instruction=self._system_prompt,
        )

    def _load_system_prompt(self) -> str:
        prompt_path = Path(os.environ.get("GEMINI_SYSTEM_PROMPT_PATH", _DEFAULT_SYSTEM_PROMPT_PATH))
        return prompt_path.read_text(encoding="utf-8")

    def diagnose(
        self,
        issue_type: str,
        log_chunk: List[str],
        known_patterns: List[Dict],
        fix_strategies: Dict,
    ) -> Tuple[Optional[Dict], List[Dict], Dict]:
        """
        Ask Gemini to diagnose an issue from a log chunk.

        Same interface as ClaudeClient.diagnose() so both clients are
        interchangeable within DiagnosticAgent.

        Returns
        -------
        diagnosis
            Structured dict ready for the remediation agent, or None on failure.
        new_patterns
            New issue patterns Gemini identified; empty list when none found.
        new_fix_strategies
            New fix strategies Gemini identified; empty dict when none found.
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

        generation_config = {"max_output_tokens": 4096}
        response = self._model.generate_content(
            json.dumps(payload, indent=2),
            generation_config=generation_config,
        )
        raw = (response.text or "").strip()

        if not raw:
            raise ValueError(
                f"Gemini returned no text content "
                f"(finish_reason={getattr(response.candidates[0], 'finish_reason', 'unknown') if response.candidates else 'no candidates'})"
            )

        # Strip markdown code fences that Gemini sometimes wraps around JSON.
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)

        data = json.loads(raw)
        diagnosis = data.get("diagnosis")
        new_patterns = data.get("new_patterns") or []
        new_fix_strategies = data.get("new_fix_strategies") or {}
        return diagnosis, new_patterns, new_fix_strategies
