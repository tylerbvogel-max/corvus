"""Risk category patterns and grounding reference pattern for the agentic-memory domain."""

import re

RISK_CATEGORIES: dict[str, list[tuple[re.Pattern, str]]] = {
    "destructive_operation": [
        (re.compile(r"\b(rm\s+-rf?|drop\s+(?:table|database)|force[- ]push|git\s+push\s+.*--force|truncate\s+table|drop_all)\b", re.I),
         "References a destructive operation; memories about these need extra confirmations before auto-apply"),
        (re.compile(r"\b(delete[sd]?\s+(?:the\s+)?(?:database|repo|branch|remote)|wiped?|irreversibl)", re.I),
         "References irreversible data loss"),
    ],
    "secret_exposure": [
        (re.compile(r"\b(api[_ ]?key|password|token|credential|secret)\b", re.I),
         "References credential material; must never be stored verbatim in a memory"),
        (re.compile(r"(sk-ant-|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[baprs]-)", re.I),
         "Contains a credential-shaped literal"),
    ],
    "cross_scope": [
        (re.compile(r"\b(all\s+projects|every\s+(?:repo|project)|globally|machine[- ]wide)\b", re.I),
         "Claims global scope; verify it is not a contextual truth that should be scoped-by a project"),
    ],
    "speculative": [
        (re.compile(r"\b(I\s+think|I\s+believe|it\s+(?:seems?|appears?)\s+(?:that|like)|probably|possibly|might\s+be|could\s+be|not\s+(?:entirely\s+)?sure)\b", re.I),
         "Contains hedging/speculative language; not evidence-backed"),
        (re.compile(r"\b(untested|unverified|didn't\s+(?:run|verify|check)|assum(?:e|ing|ed))\b", re.I),
         "Explicitly unverified claim"),
    ],
}

GROUNDING_REF_PATTERN = re.compile(
    r'(?:\bsession[:\s]+[0-9a-f-]{8,}|\b[\w./-]+\.(?:py|ts|tsx|js|md|yaml|yml|json|sh|sql):\d+'
    r'|\bexit(?:\s+code)?\s+\d+\b|\[\[[a-z0-9][a-z0-9_-]+\]\]|\bepisode[:\s]+[0-9a-f-]{8,})',
    re.I,
)
