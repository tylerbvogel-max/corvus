"""Multi-provider LLM abstraction layer.

Unified interface for calling LLMs across providers: OpenAI via Codex CLI,
Anthropic, Google Gemini, Groq, and Azure OpenAI. All callers use display
names from MODEL_REGISTRY.
Provider dispatch is automatic based on the model's registered provider.

Model aliases (LLM_MODEL_ALIASES env var) allow environment-specific redirection
without changing call sites. The default config maps the three Anthropic grade
names to same-grade Codex models; one environment override reverses the cutover.

Free-tier models are prioritized in the registry ordering for UI display.
"""

import asyncio
import json
import logging
import glob
import os
import shutil
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType

from app.config import settings

# Claude CLI path — personal subscription, no API credits consumed.
#
# The default used to hardcode ~/.config/nvm/versions/node/v20.20.0/bin/claude.
# That baked one machine's Node version into the application: it broke whenever
# nvm moved (the machine convention is now 22.22.0), and inside the container it
# resolved under the runtime user's home where nothing is installed. Resolve
# instead, and fall back to "" — os.path.exists("") is False, so an unresolved
# CLI reports the provider as simply unavailable rather than crashing at call
# time or silently pointing at a stale interpreter.
def _resolve_claude_cli() -> str:
    explicit = os.environ.get("CLAUDE_CLI_PATH")
    if explicit:
        return os.path.expanduser(explicit)
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    # Legacy nvm layout: prefer the highest Node version that has the CLI, so a
    # machine carrying several nvm installs does not get pinned to an old one.
    candidates = sorted(
        glob.glob(os.path.expanduser("~/.config/nvm/versions/node/*/bin/claude")),
        key=lambda p: [
            int(part) if part.isdigit() else part
            for part in p.split("/node/v")[-1].split("/")[0].split(".")
        ],
    )
    return candidates[-1] if candidates else ""


_CLAUDE_CLI_PATH = _resolve_claude_cli()

# Codex CLI path — OpenAI personal subscription (same posture as the Claude
# CLI: no API credits). Shared with codex_usage.py's CODEX_PATH convention.
_CODEX_CLI_PATH = os.path.expanduser(
    os.environ.get("CODEX_PATH", "~/.local/bin/codex"))

# Per-request reasoning effort (low|medium|high), set by the API layer and
# inherited by concurrent slot tasks. None -> fall back to settings.default_effort.
# Whitelisted before use (it becomes a CLI arg) — never interpolate raw input.
effort_var: ContextVar[str | None] = ContextVar("llm_effort", default=None)
_VALID_EFFORT = ("low", "medium", "high")

logger = logging.getLogger(__name__)


# ── Model Registry ──

@dataclass(frozen=True)
class ModelInfo:
    display_name: str       # Short name used throughout the codebase
    provider: str           # "anthropic" | "google" | "groq" | "azure_openai"
    api_id: str             # Provider-specific model ID
    input_price: float      # USD per million input tokens
    output_price: float     # USD per million output tokens
    tier: str               # "frontier" | "free" — for UI grouping
    # Per-model input context window in tokens. Drives the hero-page
    # token-vs-context bar denominator. Values are the advertised input
    # context the API accepts; output limits are separate and not tracked here.
    context_window_tokens: int = 200_000


MODEL_REGISTRY: MappingProxyType[str, ModelInfo] = MappingProxyType({
    # ── Frontier (paid, high-capability lab models) ──
    "haiku": ModelInfo(
        display_name="haiku",
        provider="anthropic",
        api_id="claude-haiku-4-5-20251001",
        # Haiku 4.5 list price (verified 2026-07-08); 0.80/4.00 was Haiku 3.5
        input_price=1.00,
        output_price=5.00,
        tier="frontier",
        context_window_tokens=200_000,
    ),
    "sonnet": ModelInfo(
        display_name="sonnet",
        provider="anthropic",
        api_id="claude-sonnet-4-6",
        input_price=3.00,
        output_price=15.00,
        tier="frontier",
        context_window_tokens=200_000,
    ),
    "opus": ModelInfo(
        display_name="opus",
        provider="anthropic",
        api_id="claude-opus-4-6",
        # Opus 4.5+ list price (verified 2026-07-08); 15/75 was Opus 4.1-era —
        # 3x too high, inflating every historical opus cost estimate
        input_price=5.00,
        output_price=25.00,
        tier="frontier",
        context_window_tokens=200_000,
    ),
    "gemini-pro": ModelInfo(
        display_name="gemini-pro",
        provider="google",
        api_id="gemini-2.5-pro",
        input_price=1.25,
        output_price=10.00,
        tier="frontier",
        context_window_tokens=1_048_576,  # Gemini 2.5 Pro advertised 1M input tokens
    ),
    # ── Free (generous free tiers, suitable for real usage) ──
    "gemini-flash": ModelInfo(
        display_name="gemini-flash",
        provider="google",
        api_id="gemini-2.0-flash",
        input_price=0.0,
        output_price=0.0,
        tier="free",
        context_window_tokens=1_048_576,
    ),
    "gemini-flash-lite": ModelInfo(
        display_name="gemini-flash-lite",
        provider="google",
        api_id="gemini-2.0-flash-lite",
        input_price=0.0,
        output_price=0.0,
        tier="free",
        context_window_tokens=1_048_576,
    ),
    # ── Groq (LPU — free tier, rate-limited) ──
    # These are priced 0.0 because THIS ACCOUNT IS ON GROQ'S FREE TIER: no card,
    # no credits, no per-token charge — you are gated by rate limits, not billing.
    # Verified 2026-07-12 from live response headers: x-ratelimit-limit-requests
    # 14400/day, x-ratelimit-limit-tokens 6000/min (Groq's documented free profile).
    #
    # THE BINDING CONSTRAINT IS THROUGHPUT, NOT COST. 6,000 tokens/minute is small
    # — smaller than a single large graph-context prompt. Do not route the hero
    # query path or any big-context call here without checking TPM headroom first;
    # it will 429, not overspend.
    #
    # If a card is ever added to Groq (Developer tier: 10x rate limits, ~25% off
    # list), these zeros become a silent under-report and MUST be set to the real
    # per-1M rates — as of 2026-07-12: scout 0.11/0.34, 70b 0.59/0.79, 8b 0.05/0.08.
    #
    # Meta retired its first-party Llama API on 2026-07-06; Groq is the Llama path.
    "groq-llama-4-scout": ModelInfo(
        display_name="groq-llama-4-scout",
        provider="groq",
        api_id="meta-llama/llama-4-scout-17b-16e-instruct",
        input_price=0.0,
        output_price=0.0,
        tier="free",
        context_window_tokens=131_072,  # Llama 4 Scout 17Bx16E: 128K
    ),
    "groq-llama-70b": ModelInfo(
        display_name="groq-llama-70b",
        provider="groq",
        api_id="llama-3.3-70b-versatile",
        input_price=0.0,
        output_price=0.0,
        tier="free",
        context_window_tokens=131_072,  # Llama 3.3 70B: 128K
    ),
    "groq-llama-8b": ModelInfo(
        display_name="groq-llama-8b",
        provider="groq",
        api_id="llama-3.1-8b-instant",
        input_price=0.0,
        output_price=0.0,
        tier="free",
        context_window_tokens=131_072,
    ),
    # groq-gemma-9b (gemma2-9b-it) REMOVED 2026-07-12: Groq no longer serves it.
    # Confirmed absent from GET /openai/v1/models — any call would have 404'd.
    # ── Azure OpenAI (GovCloud / enterprise deployments) ──
    "azure-gpt4o": ModelInfo(
        display_name="azure-gpt4o",
        provider="azure_openai",
        api_id="gpt-4o",
        input_price=2.50,
        output_price=10.00,
        tier="frontier",
        context_window_tokens=128_000,
    ),
    "azure-gpt4o-mini": ModelInfo(
        display_name="azure-gpt4o-mini",
        provider="azure_openai",
        api_id="gpt-4o-mini",
        input_price=0.15,
        output_price=0.60,
        tier="frontier",
        context_window_tokens=128_000,
    ),
    "azure-o1": ModelInfo(
        display_name="azure-o1",
        provider="azure_openai",
        api_id="o1",
        input_price=15.00,
        output_price=60.00,
        tier="frontier",
        context_window_tokens=200_000,
    ),
    # ── OpenAI via Codex CLI (personal subscription, no API credits) ──
    # Same posture as the anthropic provider: the CLI bills against the
    # user's OpenAI subscription, prices below are API-equivalent list
    # rates (verified 2026-07-17 vs the July 2026 GA announcement) so the
    # ledger reports "what would a customer pay". Slugs + effort levels +
    # 272k context read from ~/.codex/models_cache.json on this machine.
    "codex-sol": ModelInfo(
        display_name="codex-sol",
        provider="openai_codex",
        api_id="gpt-5.6-sol",
        input_price=5.00,
        output_price=30.00,
        tier="frontier",
        context_window_tokens=272_000,
    ),
    "codex-terra": ModelInfo(
        display_name="codex-terra",
        provider="openai_codex",
        api_id="gpt-5.6-terra",
        input_price=2.50,
        output_price=15.00,
        tier="frontier",
        context_window_tokens=272_000,
    ),
    "codex-luna": ModelInfo(
        display_name="codex-luna",
        provider="openai_codex",
        api_id="gpt-5.6-luna",
        input_price=1.00,
        output_price=6.00,
        tier="frontier",
        context_window_tokens=272_000,
    ),
})

# No default — user must always select a model explicitly
DEFAULT_MODEL: str | None = None

# ── Cross-provider same-grade fallback ──
# Codex is primary by default through LLM_MODEL_ALIASES; Anthropic remains the
# reverse-role fallback while its subscription is available. The bidirectional
# crosswalk also preserves the single-setting rollback: LLM_MODEL_ALIASES={}
# makes Anthropic primary and Codex fallback again. Grades are matched on the
# providers' positioning and API-equivalent price, not vibes:
# opus $5/$25 ↔ sol $5/$30, sonnet $3/$15 ↔ terra $2.50/$15,
# haiku $1/$5 ↔ luna $1/$6. Effort maps 1:1.
FALLBACK_CHAINS: MappingProxyType[str, tuple[str, ...]] = MappingProxyType({
    "opus": ("codex-sol",),
    "sonnet": ("codex-terra",),
    "haiku": ("codex-luna",),
    "codex-sol": ("opus",),
    "codex-terra": ("sonnet",),
    "codex-luna": ("haiku",),
})

# Error signatures that mean "the provider is lapsed/locked, not the request
# is bad" — these justify falling through the chain and cooling the provider
# down. Anything else re-raises: a parse bug must fail loudly, not silently
# hop providers.
_LAPSE_ERROR_MARKERS = (
    "credit balance", "usage limit", "hit your limit", "rate limit", "quota",
    "unauthorized", "authentication", "not logged in", "login",
    "expired", "billing", "subscription", "payment",
)
_PROVIDER_COOLDOWN_SECONDS = 30 * 60
# provider -> monotonic deadline; while in the future, skip straight to fallbacks
_provider_down_until: dict[str, float] = {}


# ── Provider availability ──

def _provider_available(provider: str) -> bool:
    """Check if a provider's API key is configured."""
    assert isinstance(provider, str), "provider must be a string"
    key_map = MappingProxyType({
        "anthropic": "cli" if os.path.exists(_CLAUDE_CLI_PATH) else "",
        "openai_codex": "cli" if os.path.exists(_CODEX_CLI_PATH) else "",
        "google": settings.google_api_key,
        "groq": settings.groq_api_key,
        "azure_openai": settings.azure_openai_api_key,
    })
    key = key_map.get(provider, "")
    assert provider in key_map, f"Unknown provider: {provider}"
    return bool(key and key.strip())


def get_available_models() -> list[dict]:
    """Return list of models whose provider API key is configured."""
    result = []
    for name, info in MODEL_REGISTRY.items():
        if _provider_available(info.provider):
            effective = _resolve_alias(name)
            effective_info = MODEL_REGISTRY.get(effective, info)
            result.append({
                "display_name": info.display_name,
                "provider": info.provider,
                "api_id": info.api_id,
                "tier": info.tier,
                "input_price": info.input_price,
                "output_price": info.output_price,
                "context_window_tokens": info.context_window_tokens,
                # Keep aliases observable: callers may still send a legacy
                # grade name, but the roster must disclose what will serve it.
                "effective_model": effective,
                "effective_provider": effective_info.provider,
                "is_primary": info.provider == "openai_codex",
            })
    # Product surfaces select the first model as their fallback/default.
    # Put the active primary provider first without mutating registry order.
    result.sort(key=lambda row: (not row["is_primary"], row["display_name"]))
    assert isinstance(result, list), "result must be a list"
    return result


def get_valid_model_names() -> set[str]:
    """Return set of model display names that are currently available."""
    return {
        name for name, info in MODEL_REGISTRY.items()
        if _provider_available(info.provider)
    }


# ── Provider implementations ──

def _build_anthropic_args(
    model_info: ModelInfo, system_prompt: str, effort: str, session: dict | None,
) -> list[str]:
    """Build the claude CLI argv. Pure — unit-testable without a subprocess.

    session = {"session_id": <uuid>, "resume": bool} enables CLI session
    persistence (hero chat): --session-id creates, --resume continues, and
    --no-session-persistence is dropped so the transcript survives the call.
    Resumed sessions get the conversation prefix served from the prompt cache
    (measured: 17.8k tokens read at 0.1x instead of re-sent). Stateless calls
    keep today's exact flags. The user message always goes over STDIN, never
    argv: prompts that start with "-" would parse as CLI options.
    """
    args = [
        _CLAUDE_CLI_PATH, "-p",
        "--model", model_info.api_id,
        "--output-format", "json",
        "--strict-mcp-config",
        # Disable ALL built-in CLI tools: every Corvus call is a pure
        # completion (classify/judge/agent envelopes are text protocols, never
        # CLI tool use), yet the tool schemas cost ~17.8k prompt tokens per
        # call (measured 2026-07-08: 17,785 cache-create with tools vs 180
        # plain input without).
        "--tools", "",
    ]
    if session:
        sid = session.get("session_id")
        assert sid, "session requires a session_id"
        args.extend(["--resume" if session.get("resume") else "--session-id", sid])
    else:
        args.append("--no-session-persistence")
    if system_prompt:
        args.extend(["--system-prompt", system_prompt])
    if effort in _VALID_EFFORT:
        args.extend(["--effort", effort])
    return args


async def _anthropic_chat(
    system_prompt: str, user_message: str, max_tokens: int, model_info: ModelInfo,
    session: dict | None = None,
) -> dict:
    """Call Claude via the local Claude CLI (personal subscription, no API credits).

    The subprocess must behave as a PURE completion model, so it is isolated
    from the agentic CLI environment three ways:
    - cwd=/tmp: prevents pickup of the repo's CLAUDE.md / .mcp.json (a CLI
      launched in the repo loads the Corvus MCP config and answers with
      "I need permission to access the neuron graph" instead of classifying)
    - --strict-mcp-config with no --mcp-config: zero MCP servers
    - --system-prompt (not --append-): replaces the CLI's default
      software-engineering-agent framing entirely
    """
    assert len(user_message.strip()) > 0, "user_message must be non-empty"

    effort = effort_var.get() or settings.default_effort
    args = _build_anthropic_args(model_info, system_prompt, effort, session)

    # Strip CLAUDECODE/CLAUDE_CODE_* from env — the CLI refuses to launch nested
    # inside another Claude Code session. See CLAUDE.md "Claude CLI nested session".
    child_env = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDECODE") and not k.startswith("CLAUDE_CODE_")}
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=child_env, cwd="/tmp",
    )
    stdout, stderr = await proc.communicate(input=user_message.encode())
    if proc.returncode != 0:
        # Claude CLI reports subscription limits as a JSON result on stdout
        # while leaving stderr empty. Preserve whichever stream has the
        # receipt so _is_lapse_error can route to the same-grade fallback.
        detail = (stderr or stdout).decode(errors="replace")[:500]
        raise AssertionError(
            f"claude CLI failed (exit {proc.returncode}): {detail}")

    payload = json.loads(stdout.decode())
    text = payload.get("result") or payload.get("text") or ""
    usage = payload.get("usage") or {}
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    cache_create = int(usage.get("cache_creation_input_tokens", 0) or 0)
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)

    assert input_tokens >= 0, f"input_tokens must be non-negative, got {input_tokens}"
    assert output_tokens >= 0, f"output_tokens must be non-negative, got {output_tokens}"
    # Cost is an API-equivalent estimate. The CLI itself runs on a personal
    # subscription (no per-call charge), but the UI needs the "what would a
    # customer pay?" figure — so price it against MODEL_REGISTRY rates with
    # Anthropic's cache multipliers (1.25x create, 0.10x read).
    cost = _estimate_cost_anthropic(
        model_info, input_tokens, cache_create, cache_read, output_tokens,
    )
    result = {
        "text": text,
        "input_tokens": input_tokens,
        "cache_creation_tokens": cache_create,
        "cache_read_tokens": cache_read,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "model_version": payload.get("model") or model_info.api_id,
    }
    if session:
        # The CLI's returned id is authoritative (resume may fork a session).
        result["session_id"] = payload.get("session_id") or session.get("session_id")
    return result


def _build_codex_args(model_info: ModelInfo, effort: str) -> list[str]:
    """Build the codex CLI argv. Pure — unit-testable without a subprocess.

    Mirrors the anthropic isolation posture: read-only sandbox (Corvus calls
    are pure completions, never agentic shell work), --ephemeral (no session
    persistence), --skip-git-repo-check (cwd is /tmp). Corvus effort levels
    map 1:1 onto codex reasoning efforts (both speak low|medium|high).
    The prompt goes over STDIN ('-' positional), never argv."""
    args = [
        _CODEX_CLI_PATH, "exec", "--json",
        "-m", model_info.api_id,
        "-s", "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
    ]
    if effort in _VALID_EFFORT:
        args.extend(["-c", f"model_reasoning_effort={effort}"])
    args.append("-")
    return args


def _parse_codex_events(stdout: str) -> tuple[str, dict]:
    """(final agent text, usage dict) from codex exec --json JSONL events."""
    text = ""
    usage: dict = {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "item.completed":
            item = event.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text") or text
        elif event.get("type") == "turn.completed":
            usage = event.get("usage") or {}
    return text, usage


async def _codex_chat(
    system_prompt: str, user_message: str, max_tokens: int, model_info: ModelInfo,
) -> dict:
    """Call an OpenAI model via the local Codex CLI (subscription, no credits).

    Isolation mirrors _anthropic_chat: cwd=/tmp keeps the subprocess out of
    any repo's agentic context; CLAUDE*/CODEX* env stripped so neither CLI's
    nested-session detection can fire. codex exec has no --system-prompt
    flag, so the system prompt is framed into the stdin payload — Corvus
    system prompts are task protocols (judge/classify envelopes), and the
    framing survives them. NOTE: the codex base agent context costs ~13k
    input tokens per call (measured 2026-07-17, mostly cache-served);
    model-usage telemetry keeps that overhead visible now that this is the
    primary provider."""
    assert len(user_message.strip()) > 0, "user_message must be non-empty"

    effort = effort_var.get() or settings.default_effort
    args = _build_codex_args(model_info, effort)
    payload_in = (
        f"SYSTEM INSTRUCTIONS (follow these for this task):\n{system_prompt}\n\n"
        f"USER MESSAGE:\n{user_message}"
    ) if system_prompt else user_message

    child_env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("CLAUDECODE", "CLAUDE_CODE_", "CODEX_"))
    }
    proc = await asyncio.create_subprocess_exec(
        *args, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env=child_env, cwd="/tmp",
    )
    stdout, stderr = await proc.communicate(input=payload_in.encode())
    assert proc.returncode == 0, (
        f"codex CLI failed (exit {proc.returncode}): "
        f"{stderr.decode(errors='replace')[:500]}"
    )
    text, usage = _parse_codex_events(stdout.decode())
    input_tokens = int(usage.get("input_tokens", 0) or 0)
    cached = int(usage.get("cached_input_tokens", 0) or 0)
    output_tokens = int(usage.get("output_tokens", 0) or 0)
    assert input_tokens >= 0 and output_tokens >= 0, "token counts must be non-negative"
    # API-equivalent estimate; OpenAI cached input bills at 0.1x list.
    cost = (
        (input_tokens - cached) * model_info.input_price
        + cached * model_info.input_price * 0.10
        + output_tokens * model_info.output_price
    ) / 1_000_000
    return {
        "text": text,
        "input_tokens": input_tokens,
        "cache_creation_tokens": 0,
        "cache_read_tokens": cached,
        "output_tokens": output_tokens,
        "cost_usd": max(cost, 0.0),
        "model_version": model_info.api_id,
    }


# Effort → Gemini 2.5 thinking-budget tokens (monotone; 128 is the 2.5 Pro
# minimum, effectively "think as little as allowed"). Applied only to models
# that support thinking — gemini-2.0 rejects thinking_config.
_GEMINI_THINKING_BUDGET = MappingProxyType({"low": 128, "medium": 8192, "high": 24576})


async def _google_chat(
    system_prompt: str, user_message: str, max_tokens: int, model_info: ModelInfo,
) -> dict:
    """Call Google Gemini API via the google-generativeai SDK."""
    from google import genai

    assert settings.google_api_key, "GOOGLE_API_KEY not configured"
    assert len(user_message.strip()) > 0, "user_message must be non-empty"

    thinking_config = None
    if model_info.api_id.startswith("gemini-2.5"):
        effort = effort_var.get() or settings.default_effort
        budget = _GEMINI_THINKING_BUDGET.get(effort)
        if budget is not None:
            thinking_config = genai.types.ThinkingConfig(thinking_budget=budget)

    client = genai.Client(api_key=settings.google_api_key)
    config = genai.types.GenerateContentConfig(
        system_instruction=system_prompt if system_prompt else None,
        max_output_tokens=max_tokens,
        thinking_config=thinking_config,
    )
    response = await client.aio.models.generate_content(
        model=model_info.api_id,
        contents=user_message,
        config=config,
    )

    text = response.text or ""
    input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
    output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
    cost = estimate_cost(model_info.display_name, input_tokens, output_tokens)

    assert input_tokens >= 0, f"input_tokens must be non-negative, got {input_tokens}"
    assert output_tokens >= 0, f"output_tokens must be non-negative, got {output_tokens}"
    return {
        "text": text,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "model_version": model_info.api_id,
    }


async def _groq_chat(
    system_prompt: str, user_message: str, max_tokens: int, model_info: ModelInfo,
) -> dict:
    """Call Groq API via the groq SDK (OpenAI-compatible)."""
    from groq import AsyncGroq

    assert settings.groq_api_key, "GROQ_API_KEY not configured"
    assert len(user_message.strip()) > 0, "user_message must be non-empty"

    client = AsyncGroq(api_key=settings.groq_api_key)
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_message})

    try:
        response = await client.chat.completions.create(
            model=model_info.api_id,
            messages=messages,
            max_tokens=max_tokens,
        )
    finally:
        await client.close()

    text = response.choices[0].message.content if response.choices else ""
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    cost = estimate_cost(model_info.display_name, input_tokens, output_tokens)

    assert input_tokens >= 0, f"input_tokens must be non-negative, got {input_tokens}"
    assert output_tokens >= 0, f"output_tokens must be non-negative, got {output_tokens}"
    return {
        "text": text or "",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "model_version": response.model if response.model else model_info.api_id,
    }


def _azure_deployment_name(api_id: str) -> str:
    """Map model api_id to Azure OpenAI deployment name from settings."""
    deploy_map = MappingProxyType({
        "gpt-4o": settings.azure_openai_deployment_gpt4o,
        "gpt-4o-mini": settings.azure_openai_deployment_gpt4o_mini,
        "o1": settings.azure_openai_deployment_o1,
    })
    deployment = deploy_map.get(api_id, "")
    assert deployment, (
        f"No Azure deployment configured for model {api_id!r}. "
        f"Set AZURE_OPENAI_DEPLOYMENT_* in .env."
    )
    return deployment


def _build_azure_messages(
    system_prompt: str, user_message: str, is_o1: bool,
) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Build messages list and token-limit key for Azure OpenAI.

    o1 models do not support system messages and use max_completion_tokens.
    """
    messages: list[dict[str, str]] = []
    if is_o1:
        if system_prompt:
            messages.append({"role": "user", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        token_key = "max_completion_tokens"
    else:
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        token_key = "max_tokens"
    assert len(messages) >= 1, "messages must contain at least one entry"
    return messages, token_key


async def _azure_openai_chat(
    system_prompt: str, user_message: str, max_tokens: int,
    model_info: ModelInfo, timeout: int = 180,
) -> dict:
    """Call Azure OpenAI API via the openai SDK."""
    from openai import AsyncAzureOpenAI

    assert settings.azure_openai_api_key, "AZURE_OPENAI_API_KEY not configured"
    assert settings.azure_openai_endpoint, "AZURE_OPENAI_ENDPOINT not configured"
    assert len(user_message.strip()) > 0, "user_message must be non-empty"

    deployment = _azure_deployment_name(model_info.api_id)
    is_o1 = model_info.api_id.startswith("o1")
    messages, token_key = _build_azure_messages(system_prompt, user_message, is_o1)

    # o1 is a reasoning model: map the slot/request effort straight through.
    extra_kwargs: dict = {}
    effort = effort_var.get() or settings.default_effort
    if is_o1 and effort in _VALID_EFFORT:
        extra_kwargs["reasoning_effort"] = effort

    client = AsyncAzureOpenAI(
        api_key=settings.azure_openai_api_key,
        azure_endpoint=settings.azure_openai_endpoint,
        api_version=settings.azure_openai_api_version,
        timeout=float(timeout),
    )
    try:
        response = await client.chat.completions.create(
            model=deployment, messages=messages, **{token_key: max_tokens}, **extra_kwargs,
        )
    finally:
        await client.close()

    text = response.choices[0].message.content if response.choices else ""
    usage = response.usage
    input_tokens = usage.prompt_tokens if usage else 0
    output_tokens = usage.completion_tokens if usage else 0
    cost = estimate_cost(model_info.display_name, input_tokens, output_tokens)

    assert input_tokens >= 0, f"input_tokens must be non-negative, got {input_tokens}"
    assert output_tokens >= 0, f"output_tokens must be non-negative, got {output_tokens}"
    return {
        "text": text or "",
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": cost,
        "model_version": response.model if response.model else model_info.api_id,
    }


# ── Model alias resolution ──

def _resolve_alias(model: str) -> str:
    """Resolve model alias from LLM_MODEL_ALIASES env var.

    If aliases are configured and the model name matches, returns the target.
    Otherwise returns the original model name unchanged.
    """
    assert isinstance(model, str), "model must be a string"
    alias_json = settings.llm_model_aliases
    if not alias_json or not alias_json.strip():
        return model
    try:
        aliases = json.loads(alias_json)
    except json.JSONDecodeError:
        logger.warning("LLM_MODEL_ALIASES is not valid JSON, ignoring: %s", alias_json)
        return model
    assert isinstance(aliases, dict), "LLM_MODEL_ALIASES must be a JSON object"
    resolved = aliases.get(model, model)
    if resolved != model:
        logger.debug("Model alias: %s -> %s", model, resolved)
    return resolved


# ── Provider dispatch ──

_PROVIDER_DISPATCH = MappingProxyType({
    "anthropic": _anthropic_chat,
    "openai_codex": _codex_chat,
    "google": _google_chat,
    "groq": _groq_chat,
    "azure_openai": _azure_openai_chat,
})


def _is_lapse_error(exc: BaseException) -> bool:
    """Provider-lapsed (auth/quota/billing) vs request-bug. Only the former
    justifies walking the fallback chain."""
    text = str(exc).casefold()
    return any(marker in text for marker in _LAPSE_ERROR_MARKERS)


def _provider_in_cooldown(provider: str) -> bool:
    import time
    return _provider_down_until.get(provider, 0.0) > time.monotonic()


def _mark_provider_down(provider: str, why: str) -> None:
    import time
    _provider_down_until[provider] = time.monotonic() + _PROVIDER_COOLDOWN_SECONDS
    logger.warning("provider %s marked down for %ds: %s",
                   provider, _PROVIDER_COOLDOWN_SECONDS, why[:200])


def _fallback_candidates(resolved: str) -> list[str]:
    """[primary, *same-grade fallbacks] — see FALLBACK_CHAINS crosswalk."""
    return [resolved, *FALLBACK_CHAINS.get(resolved, ())]


async def _call_provider(
    model_info: ModelInfo, system_prompt: str, user_message: str,
    max_tokens: int, timeout: int, session: dict | None,
) -> dict:
    """Dispatch one call to one provider's handler."""
    handler = _PROVIDER_DISPATCH.get(model_info.provider)
    assert handler is not None, f"No handler for provider: {model_info.provider}"
    # Azure OpenAI handler accepts timeout; others ignore it for now.
    # Session persistence is a Claude-CLI capability only — other providers
    # run stateless and return no session_id (callers fall back to packing
    # history into the message).
    if model_info.provider == "anthropic":
        return await handler(system_prompt, user_message, max_tokens, model_info, session)
    if model_info.provider == "azure_openai":
        return await handler(system_prompt, user_message, max_tokens, model_info, timeout)
    return await handler(system_prompt, user_message, max_tokens, model_info)


async def llm_chat(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 2048,
    model: str | None = None,
    timeout: int = 180,
    session: dict | None = None,
    workload: str = "corvus_internal",
    harness: str = "corvus_backend",
    effort: str | None = None,
) -> dict:
    """Call an LLM and return {"text", "input_tokens", "output_tokens", "cost_usd", "model_version"}.

    `model` must be a MODEL_REGISTRY key (e.g. "haiku", "gemini-flash", "azure-gpt4o").
    Model aliases from LLM_MODEL_ALIASES are resolved before lookup.
    No default model — caller must specify explicitly.

    `effort` (low|medium|high) overrides the ambient effort_var/default for
    this call only — the per-workload quality dial (e.g. the janitor's
    sonnet@low pair judge).

    Fallback: if the effective model's provider is unavailable, cooling down
    after a lapse-shaped failure, or fails THIS call with a lapse-shaped error
    (auth/quota/billing), the same-grade FALLBACK_CHAINS entry is tried.
    With the default aliases that means codex-terra -> sonnet; with
    LLM_MODEL_ALIASES={} it becomes sonnet -> codex-terra. Request-shaped
    errors re-raise immediately; they never hop providers.
    """
    assert model is not None, "model must be specified — no default model selection"
    assert (system_prompt and system_prompt.strip()) or (user_message and user_message.strip()), \
        "llm_chat requires a non-empty system_prompt or user_message"
    assert effort is None or effort in _VALID_EFFORT, f"invalid effort: {effort!r}"

    resolved = _resolve_alias(model)
    if resolved not in MODEL_REGISTRY:
        raise ValueError(
            f"Unknown model: {resolved!r}"
            f"{' (aliased from ' + model + ')' if resolved != model else ''}. "
            f"Available: {list(MODEL_REGISTRY.keys())}"
        )

    effort_token = effort_var.set(effort) if effort is not None else None
    try:
        result, served_by = await _chat_with_fallback(
            resolved, system_prompt, user_message, max_tokens, timeout, session)
    finally:
        if effort_token is not None:
            effort_var.reset(effort_token)

    assert "text" in result and "input_tokens" in result and "output_tokens" in result, \
        "llm_chat result missing required keys"
    assert result["input_tokens"] >= 0, f"input_tokens must be non-negative, got {result['input_tokens']}"
    assert result["output_tokens"] >= 0, f"output_tokens must be non-negative, got {result['output_tokens']}"
    # Attribution receipt: which registry entry actually served the call.
    # Callers with provider-integrity requirements (eval certificates) need
    # this in-band, not just in the usage ledger.
    result["served_by"] = served_by
    result["provider"] = MODEL_REGISTRY[served_by].provider
    from app.services.model_usage_ledger import record_model_usage
    record_model_usage(provider=MODEL_REGISTRY[served_by].provider, model=served_by,
                       harness=harness, workload=workload, result=result)
    return result


async def _chat_with_fallback(
    resolved: str, system_prompt: str, user_message: str,
    max_tokens: int, timeout: int, session: dict | None,
) -> tuple[dict, str]:
    """Walk [primary, *fallbacks]; return (result, served-by model key)."""
    last_error: Exception | None = None
    candidates = _fallback_candidates(resolved)
    for name in candidates:
        info = MODEL_REGISTRY[name]
        if not _provider_available(info.provider):
            last_error = last_error or ValueError(
                f"Provider {info.provider!r} not configured for model {name!r}.")
            continue
        if _provider_in_cooldown(info.provider):
            continue
        try:
            result = await _call_provider(
                info, system_prompt, user_message, max_tokens, timeout,
                session if info.provider == "anthropic" else None)
        except Exception as exc:  # lapse-shaped -> next candidate; else raise
            if name != candidates[-1] and _is_lapse_error(exc):
                _mark_provider_down(info.provider, str(exc))
                last_error = exc
                continue
            raise
        if name != resolved:
            logger.warning("llm fallback: %s -> %s (primary provider down)",
                           resolved, name)
            result["fallback_from"] = resolved
        return result, name
    raise last_error or ValueError(
        f"No available provider for {resolved!r} or its fallbacks.")


# ── Cost estimation ──

def estimate_cost(model: str | None, input_tokens: int, output_tokens: int) -> float:
    """Estimate USD cost from token counts and model name (no cache differentiation)."""
    assert input_tokens >= 0, f"input_tokens must be non-negative, got {input_tokens}"
    assert output_tokens >= 0, f"output_tokens must be non-negative, got {output_tokens}"

    if not model:
        return 0.0
    info = MODEL_REGISTRY.get(model)
    if not info:
        return 0.0
    result = (input_tokens * info.input_price + output_tokens * info.output_price) / 1_000_000

    assert result >= 0, f"estimated cost must be non-negative, got {result}"
    return result


def _estimate_cost_anthropic(
    model_info: ModelInfo,
    base_input: int,
    cache_create: int,
    cache_read: int,
    output_tokens: int,
) -> float:
    """Estimate USD cost with Anthropic prompt caching rates."""
    assert base_input >= 0, f"base_input must be non-negative, got {base_input}"
    assert cache_create >= 0, f"cache_create must be non-negative, got {cache_create}"
    assert cache_read >= 0, f"cache_read must be non-negative, got {cache_read}"
    assert output_tokens >= 0, f"output_tokens must be non-negative, got {output_tokens}"

    input_cost = (
        base_input * model_info.input_price
        + cache_create * model_info.input_price * 1.25
        + cache_read * model_info.input_price * 0.10
    )
    output_cost = output_tokens * model_info.output_price
    result = (input_cost + output_cost) / 1_000_000

    assert result >= 0, f"estimated cost must be non-negative, got {result}"
    return result
