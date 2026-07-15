"""Render one canonical skill into declared harness targets.

Markdown is the current common denominator.  The profile boundary makes any
future dialect conversion explicit instead of baking a harness into memory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def profiles() -> list[dict]:
    root = Path(__file__).resolve().parents[3] / "harness" / "profiles"
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(root.glob("*.json"))]


def project_skill(name: str, rendered_markdown: str) -> dict[str, str]:
    canonical = Path(os.path.expanduser("~/.corvus-mind/capabilities/skills")) / name / "SKILL.md"
    canonical.parent.mkdir(parents=True, exist_ok=True)
    canonical.write_text(rendered_markdown, encoding="utf-8")
    outputs = {"canonical": str(canonical)}
    for profile in profiles():
        target = Path(os.path.expanduser(profile["skill_directory"])) / name / "SKILL.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered_markdown, encoding="utf-8")
        outputs[profile["id"]] = str(target)
    return outputs


def remove_projected_skill(name: str) -> list[str]:
    removed: list[str] = []
    roots = [Path(os.path.expanduser("~/.corvus-mind/capabilities/skills"))]
    roots += [Path(os.path.expanduser(p["skill_directory"])) for p in profiles()]
    for root in roots:
        path = root / name / "SKILL.md"
        if path.exists():
            path.unlink()
            removed.append(str(path))
        if path.parent.is_dir() and not any(path.parent.iterdir()):
            path.parent.rmdir()
    return removed
