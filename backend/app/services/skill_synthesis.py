"""Validate and render evidence-seeking skills before harness projection.

The composer may infer a procedure from multiple lessons. Novel checks are
kept explicitly conditional; source facts must be quoted from gate-passed
lessons. This module is intentionally independent of LLM and disk access.
"""

from __future__ import annotations

import re
from typing import Any


_NAME = re.compile(r"^mind-[a-z0-9]+(?:-[a-z0-9]+)*$")
_CHECK = re.compile(r"^(check|verify|inspect|compare|confirm|determine|measure)\b", re.I)
_INSTRUCTION_OVERRIDE = re.compile(
    r"ignore (?:all |any )?(?:previous|prior|system|developer) instructions|"
    r"disregard (?:the )?(?:system|developer) (?:prompt|instructions)|"
    r"override (?:the )?(?:system|developer) (?:prompt|instructions)", re.I,
)


def validate_and_render(draft: dict[str, Any], sources: dict[int, str]) -> dict[str, Any] | None:
    """Return a renderable procedural skill, or reject unsupported output.

    This does not assert that a model's inference is true. It preserves a
    check-before-action boundary for derived steps and requires an explicit
    cross-source synthesis step before a native skill can be emitted.
    """
    try:
        name = draft["name"]
        description = draft["description"]
        task = draft["task"]
        facts = draft["facts"]
        steps = draft["steps"]
        if (not isinstance(name, str) or not _NAME.fullmatch(name)
                or draft["kind"] != "procedure"
                or not isinstance(description, str) or not description.strip()
                or not isinstance(task, str) or not task.strip()
                or not isinstance(facts, list) or not facts
                or not isinstance(steps, list) or not steps):
            return None
        text_fields = [name, description, task]
        quoted_ids: set[int] = set()
        source_quotes: list[dict[str, Any]] = []
        for fact in facts:
            source_id, quote = fact["source_id"], fact["quote"]
            if (not isinstance(source_id, int) or source_id not in sources
                    or not isinstance(quote, str) or len(quote.strip()) < 12
                    or quote not in sources[source_id]):
                return None
            quoted_ids.add(source_id)
            source_quotes.append({"source_id": source_id, "quote": quote})
            text_fields.append(quote)
        used_ids: set[int] = set()
        cross_source = False
        derived_check = False
        rendered_steps = []
        for index, step in enumerate(steps, start=1):
            when, action = step["when"], step["action"]
            ids, basis = step["source_ids"], step["basis"]
            check = step.get("check", "")
            if (not isinstance(when, str) or not when.strip()
                    or not isinstance(action, str) or not action.strip()
                    or not isinstance(ids, list) or not ids
                    or any(not isinstance(i, int) or i not in sources for i in ids)
                    or basis not in {"synthesized", "derived-check"}
                    or not isinstance(check, str) or not check.strip()):
                return None
            if basis == "derived-check":
                if not _CHECK.match(action):
                    return None
                derived_check = True
            elif not set(ids).issubset(quoted_ids):
                return None
            if basis == "synthesized" and len(set(ids)) >= 2:
                cross_source = True
            used_ids.update(ids)
            text_fields.extend([when, action, check])
            rendered_steps.append(
                f"{index}. **When:** {when.strip()} **Do:** {action.strip()} "
                f"[sources: {', '.join(map(str, sorted(set(ids))))}]\n"
                f"   - {'Verify before acting' if basis == 'derived-check' else 'Expected check'}: {check.strip()}"
            )
        if not cross_source or not derived_check:
            return None
        if any(_INSTRUCTION_OVERRIDE.search(value) for value in text_fields):
            return None
        body = (
            f"# {task.strip()}\n\n"
            "This procedure combines verified lessons. Source IDs identify the evidence; "
            "derived checks are hypotheses to verify before acting.\n\n"
            "## Decision procedure\n\n" + "\n\n".join(rendered_steps)
            + "\n\n## Source evidence\n\n"
            + "\n".join(f"- [{item['source_id']}] {item['quote']}"
                        for item in source_quotes)
        )
        return {"name": name, "description": description.strip()[:250],
                "body_markdown": body, "source_ids": sorted(used_ids),
                "source_quotes": source_quotes, "synthesis_version": 2}
    except (KeyError, TypeError, ValueError):
        return None
