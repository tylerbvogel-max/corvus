"""Deterministic fact fingerprints (kernel Phase 1A).

A fact fingerprint is the set of typed concrete signals in a lesson's
text — filesystem paths, versions, commands, env vars, ports — plus its
subject signals (known tools, write-time entities). Two lessons sharing
a concrete signal and a subject are NOMINATED as duplicate-component
candidates. Nomination queues judgment; it never authorizes fusion — the
live corpus proved similarity alone mis-orders (complementary pair @
0.832 outranked a true duplicate @ 0.807), so no single lane may decide.

Consumers:
- lesson_store write gate: sub-FUSE_SIM candidates that fingerprint-match
  an active lesson queue for review instead of silently inserting (the
  NVM duplicates entered at 0.757-0.822 cosine, under the 0.88 radar).
  The global cosine bar is NOT lowered; this is the second signal.
- review.py packet validation: every concrete signal in a composed
  synthesis must exist in some member (no invented evidence), and
  adjacent-facet signals must not leak into the synthesis.
"""

from __future__ import annotations

import re

# Two-plus segments so "and/or" and bare filenames don't fire.
_PATH_RE = re.compile(r"(?:~|/home/\w+)?(?:/[\w.@+-]+){2,}")
_VERSION_RE = re.compile(r"\bv?(\d+\.\d+(?:\.\d+)*)\b")
_ENV_RE = re.compile(r"\b([A-Z][A-Z0-9_]{2,})=")
_PORT_RE = re.compile(r"\bport[\s=:]+(\d{4,5})\b", re.I)
_CMD_RE = re.compile(
    r"\b(source|nvm|npm|npx|node|pip3?|pytest|uvicorn|systemctl|alembic|"
    r"git|docker|psql|curl|python3?)\s+(?:--?[\w-]+\s+)*([\w./~=-]+)")
# A cmd signal needs a command-shaped argument: a path/version/flag-like
# token or a known subcommand. Bare prose after a tool name ("node
# processes", "git history") is narrative, not a command — treating it
# as concrete evidence would false-fail synthesis validation.
_CMD_SUBCOMMANDS = frozenset("""use run install build dev test restart
start stop status enable disable add upgrade migrate serve""".split())
_TOKEN_RE = re.compile(r"[a-z0-9_.+-]{2,}")
_TOOL_LEXICON = frozenset("""nvm node npm npx vite tsc typescript playwright
python pytest uvicorn alembic postgres postgresql psql systemd git docker
fastapi sqlite react chromebook crostini""".split())

# Signal-kind tiers. Strong signals are near-unique to one fact; medium
# signals (a version, an env var, a port) recur across many facts and
# only corroborate.
CONCRETE_KINDS = frozenset({"path", "cmd", "version", "env", "port"})
_STRONG_KINDS = frozenset({"path", "cmd"})


def signals_of_text(text: str) -> frozenset[str]:
    """Typed concrete + tool signals extracted from free text."""
    if not text:
        return frozenset()
    out: set[str] = set()
    for m in _PATH_RE.findall(text):
        out.add(f"path:{m.rstrip('/.,').casefold()}")
    for m in _VERSION_RE.findall(text):
        out.add(f"version:{m}")
    for m in _ENV_RE.findall(text):
        out.add(f"env:{m}")
    for m in _PORT_RE.findall(text):
        out.add(f"port:{m}")
    for verb, arg in _CMD_RE.findall(text):
        arg_cf = arg.casefold()
        if arg_cf in _CMD_SUBCOMMANDS or not arg_cf.isalpha():
            out.add(f"cmd:{verb.casefold()} {arg_cf}")
    tokens = set(_TOKEN_RE.findall(text.casefold()))
    out.update(f"tool:{t}" for t in tokens & _TOOL_LEXICON)
    return frozenset(out)


def fingerprint(neuron) -> frozenset[str]:
    """Fact fingerprint of one lesson: text signals + write-time entities."""
    signals = set(signals_of_text(f"{neuron.label} {neuron.content or ''}"))
    signals.update(
        f"entity:{str(e).casefold()}" for e in (neuron.entities or []))
    return frozenset(signals)


def _kind(signal: str) -> str:
    return signal.split(":", 1)[0]


def concrete(signals: frozenset[str]) -> set[str]:
    """Just the concrete-fact signals (paths/commands/versions/envs/ports)."""
    return {s for s in signals if _kind(s) in CONCRETE_KINDS}


def subjects(signals: frozenset[str]) -> set[str]:
    """Just the subject signals (tools + entities)."""
    return {s for s in signals if _kind(s) in ("tool", "entity")}


def nominates(a, b) -> bool:
    """Should this pair queue for duplicate judgment? Requires at least one
    STRONG shared signal (path/command) plus one other shared signal, or
    three shared concrete signals — a lone shared version or env var is
    corroboration, not a nomination."""
    shared = fingerprint(a) & fingerprint(b)
    strong = {s for s in shared if _kind(s) in _STRONG_KINDS}
    shared_concrete = {s for s in shared if _kind(s) in CONCRETE_KINDS}
    if strong and len(shared) >= 2:
        return True
    return len(shared_concrete) >= 3
