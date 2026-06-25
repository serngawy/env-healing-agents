"""
Event data model for the agent pipeline.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IssueState(str, Enum):
    DETECTED = "detected"
    DIAGNOSING = "diagnosing"
    REMEDIATING = "remediating"
    RESOLVED = "resolved"
    FAILED = "failed"


@dataclass
class KnownIssuePattern:
    """Defines a known issue pattern used for detection and diagnosis."""

    type: str
    pattern: str
    severity: Severity
    auto_fix: bool
    description: str
    symptoms: List[str] = field(default_factory=list)
    common_causes: List[str] = field(default_factory=list)
    recommended_fix: Optional[str] = None
    learned_confidence: float = 0.5
    last_adjusted: Optional[str] = None
    adjustment_reason: Optional[str] = None

    def matches(self, text: str) -> bool:
        """Return True if *text* matches this pattern (case-insensitive)."""
        return bool(re.search(self.pattern, text, re.IGNORECASE))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict suitable for JSON storage."""
        d: Dict[str, Any] = {
            "type": self.type,
            "pattern": self.pattern,
            "severity": self.severity.value if isinstance(self.severity, Severity) else self.severity,
            "auto_fix": self.auto_fix,
            "description": self.description,
            "symptoms": self.symptoms,
            "common_causes": self.common_causes,
            "learned_confidence": self.learned_confidence,
        }
        if self.recommended_fix is not None:
            d["recommended_fix"] = self.recommended_fix
        if self.last_adjusted is not None:
            d["last_adjusted"] = self.last_adjusted
        if self.adjustment_reason is not None:
            d["adjustment_reason"] = self.adjustment_reason
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "KnownIssuePattern":
        """Deserialize from a plain dict (e.g. loaded from known_issues.json)."""
        severity_raw = data.get("severity", "medium")
        try:
            severity = Severity(severity_raw)
        except ValueError:
            severity = Severity.MEDIUM
        return cls(
            type=data["type"],
            pattern=data["pattern"],
            severity=severity,
            auto_fix=bool(data.get("auto_fix", False)),
            description=data.get("description", ""),
            symptoms=data.get("symptoms", []),
            common_causes=data.get("common_causes", []),
            recommended_fix=data.get("recommended_fix"),
            learned_confidence=float(data.get("learned_confidence", 0.5)),
            last_adjusted=data.get("last_adjusted"),
            adjustment_reason=data.get("adjustment_reason"),
        )


@dataclass
class LogLine:
    """A single log line with source metadata."""

    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    stream_name: str = ""
    stream_metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class Issue:
    """A detected issue with context."""

    issue_type: str
    pattern: KnownIssuePattern
    log_line: LogLine
    context: Dict[str, Any] = field(default_factory=dict)
    state: IssueState = IssueState.DETECTED
    detected_at: datetime = field(default_factory=datetime.now)
    resource_key: str = "unknown"
    attempts: int = 0
    max_attempts: int = 3


@dataclass
class Diagnosis:
    """Result of diagnosing an issue."""

    issue_type: str
    root_cause: str
    confidence: float
    severity: Severity
    evidence: List[str] = field(default_factory=list)
    recommended_fix: Optional[str] = None
    fix_parameters: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict (e.g. for passing to remediation_agent)."""
        return {
            "issue_type": self.issue_type,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
            "severity": self.severity.value if isinstance(self.severity, Severity) else self.severity,
            "evidence": self.evidence,
            "recommended_fix": self.recommended_fix,
            "fix_parameters": self.fix_parameters,
        }


@dataclass
class RemediationResult:
    """Immediate result returned by the remediation agent after executing a fix."""

    success: bool
    message: str
    fix_applied: str = ""
    issue_type: str = ""
    dry_run: bool = False


@dataclass
class RemediationOutcome:
    """Persisted record of a remediation attempt written to remediation_outcomes.json."""

    issue_type: str
    recommended_fix: str
    success: bool
    confidence_used: float
    root_cause: str
    resource_key: str = ""
    details: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "issue_type": self.issue_type,
            "recommended_fix": self.recommended_fix,
            "success": self.success,
            "confidence_used": self.confidence_used,
            "root_cause": self.root_cause,
            "resource_key": self.resource_key,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RemediationOutcome":
        return cls(
            timestamp=data.get("timestamp", ""),
            issue_type=data.get("issue_type", ""),
            recommended_fix=data.get("recommended_fix", ""),
            success=bool(data.get("success", False)),
            confidence_used=float(data.get("confidence_used", 0.0)),
            root_cause=data.get("root_cause", ""),
            resource_key=data.get("resource_key", ""),
            details=data.get("details", ""),
        )
