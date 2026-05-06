"""
Workflow policy: batch size, server hostname regex, confirmation phrase.

Load from JSON (see config/workflow_isolation.example.json) or use defaults.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Iterator, List, Optional, TypeVar

T = TypeVar("T")


DEFAULT_POLICY_PATH = "config/workflow_isolation.json"


@dataclass
class WorkflowPolicy:
    max_batch_size: int = 5
    server_hostname_regex: Optional[str] = None
    server_confirmation_phrase: str = "YES-ISOLATE-SERVER"
    xsoar_incident_type: str = "Unclassified"
    xsoar_severity: int = 2
    xsoar_custom_fields: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowPolicy":
        if not data:
            return cls()
        regex = data.get("server_hostname_regex")
        if regex is not None and not isinstance(regex, str):
            raise ValueError("server_hostname_regex must be a string or null")
        sev = data.get("xsoar_severity", 2)
        if not isinstance(sev, int) or sev < 0 or sev > 4:
            raise ValueError("xsoar_severity must be an integer 0–4")
        mbs = int(data.get("max_batch_size", 5))
        if mbs < 1:
            raise ValueError("max_batch_size must be >= 1")
        cf = data.get("xsoar_custom_fields") or {}
        if not isinstance(cf, dict):
            raise ValueError("xsoar_custom_fields must be an object")
        return cls(
            max_batch_size=mbs,
            server_hostname_regex=regex,
            server_confirmation_phrase=str(
                data.get("server_confirmation_phrase") or "YES-ISOLATE-SERVER"
            ),
            xsoar_incident_type=str(data.get("xsoar_incident_type") or "Unclassified"),
            xsoar_severity=sev,
            xsoar_custom_fields=dict(cf),
        )


def load_policy(path: Optional[str] = None) -> WorkflowPolicy:
    p = path or os.getenv("WORKFLOW_POLICY_PATH") or DEFAULT_POLICY_PATH
    if not os.path.isfile(p):
        return WorkflowPolicy()
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Policy file must contain a JSON object: {p}")
    return WorkflowPolicy.from_dict(data)


def is_null_hostname(name: Optional[str]) -> bool:
    if name is None:
        return True
    if not str(name).strip():
        return True
    return False


def matches_server_regex(hostname: str, pattern: Optional[str]) -> bool:
    if not pattern:
        return False
    try:
        return re.search(pattern, hostname) is not None
    except re.error as e:
        raise ValueError(f"Invalid server_hostname_regex: {e}") from e


def batches(items: List[T], size: int) -> Iterator[List[T]]:
    if size < 1:
        raise ValueError("batch size must be >= 1")
    for i in range(0, len(items), size):
        yield items[i : i + size]
