"""Multi-provider LLM abstraction layer.

Unified interface for calling LLMs across providers: Anthropic, Google Gemini,
Groq, and Azure OpenAI. All callers use display names from MODEL_REGISTRY.
Provider dispatch is automatic based on the model's registered provider.

Model aliases (LLM_MODEL_ALIASES env var) allow environment-specific redirection
without changing call sites — e.g. {"haiku":"azure-gpt4o-mini"} in GovCloud.

Free-tier models are prioritized in the registry ordering for UI display.
"""

import asyncio
import json
import logging
import os
from contextvars import ContextVar
from dataclasses import dataclass
from types import MappingProxyType

from app.config import settings

# Claude CLI path — personal subscription, no API credits consumed.
_CLAUDE_CLI_PATH = os.environ.get(
    "CLAUDE_CLI_PATH",
    os.path.expanduser("~/.config/nvm/versions/node/v20.20.0/bin/claude"),
)

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
        input_price=0.80,
        output_price=4.00,
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
        input_price=15.00,
        output_price=75.00,
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
    "groq-gemma-9b": ModelInfo(
        display_name="groq-gemma-9b",
        provider="groq",
        api_id="gemma2-9b-it",
        input_price=0.0,
        output_price=0.0,
        tier="free",
        context_window_tokens=8_192,  # Gemma 2 9B: 8K
    ),
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
})

# No default — user must always select a model explicitly
DEFAULT_MODEL: str | None = None


# ── Provider availability ──

def _provider_available(provider: str) -> bool:
    """Check if a provider's API key is configured."""
    assert isinstance(provider, str), "provider must be a string"
    key_map = MappingProxyType({
        "anthropic": "cli" if os.path.exists(_CLAUDE_CLI_PATH) else "",
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
            result.append({
                "display_name": info.display_name,
                "provider": info.provider,
                "api_id": info.api_id,
                "tier": info.tier,
                "input_price": info.input_price,
                "output_price": info.output_price,
                "context_window_tokens": info.context_window_tokens,
            })
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
    assert proc.returncode == 0, (
        f"claude CLI failed (exit {proc.returncode}): {stderr.decode(errors='replace')[:500]}"
    )

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
    "google": _google_chat,
    "groq": _groq_chat,
    "azure_openai": _azure_openai_chat,
})


async def llm_chat(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 2048,
    model: str | None = None,
    timeout: int = 180,
    session: dict | None = None,
) -> dict:
    """Call an LLM and return {"text", "input_tokens", "output_tokens", "cost_usd", "model_version"}.

    `model` must be a MODEL_REGISTRY key (e.g. "haiku", "gemini-flash", "azure-gpt4o").
    Model aliases from LLM_MODEL_ALIASES are resolved before lookup.
    No default model — caller must specify explicitly.
    """
    assert model is not None, "model must be specified — no default model selection"
    assert (system_prompt and system_prompt.strip()) or (user_message and user_message.strip()), \
        "llm_chat requires a non-empty system_prompt or user_message"

    resolved = _resolve_alias(model)
    model_info = MODEL_REGISTRY.get(resolved)
    if not model_info:
        raise ValueError(
            f"Unknown model: {resolved!r}"
            f"{' (aliased from ' + model + ')' if resolved != model else ''}. "
            f"Available: {list(MODEL_REGISTRY.keys())}"
        )

    if not _provider_available(model_info.provider):
        raise ValueError(
            f"Provider {model_info.provider!r} not configured. "
            f"Set the API key in .env to use model {resolved!r}."
        )

    handler = _PROVIDER_DISPATCH.get(model_info.provider)
    assert handler is not None, f"No handler for provider: {model_info.provider}"

    # Azure OpenAI handler accepts timeout; others ignore it for now.
    # Session persistence is a Claude-CLI capability only — other providers
    # run stateless and return no session_id (callers fall back to packing
    # history into the message).
    if model_info.provider == "anthropic":
        result = await handler(
            system_prompt, user_message, max_tokens, model_info, session,
        )
    elif model_info.provider == "azure_openai":
        result = await handler(
            system_prompt, user_message, max_tokens, model_info, timeout,
        )
    else:
        result = await handler(system_prompt, user_message, max_tokens, model_info)

    assert "text" in result and "input_tokens" in result and "output_tokens" in result, \
        "llm_chat result missing required keys"
    assert result["input_tokens"] >= 0, f"input_tokens must be non-negative, got {result['input_tokens']}"
    assert result["output_tokens"] >= 0, f"output_tokens must be non-negative, got {result['output_tokens']}"
    return result


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
