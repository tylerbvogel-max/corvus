"""Opt-in live-provider smoke contract.

This module is excluded from every deterministic lane. Selecting it without
explicit authorization or a model fails loudly instead of becoming a skip.
"""

from __future__ import annotations

import os

import pytest

from app.services.llm_provider import llm_chat


pytestmark = [pytest.mark.live_provider, pytest.mark.timeout(120)]


@pytest.mark.asyncio
async def test_configured_provider_returns_grounded_sentinel():
    if os.environ.get("CORVUS_RUN_LIVE_PROVIDER") != "1":
        pytest.fail("set CORVUS_RUN_LIVE_PROVIDER=1 for the live-provider lane")
    model = os.environ.get("CORVUS_LIVE_PROVIDER_MODEL", "").strip()
    if not model:
        pytest.fail("set CORVUS_LIVE_PROVIDER_MODEL to an available registry key")

    sentinel = "CORVUS-LIVE-PROVIDER-OK"
    result = await llm_chat(
        system_prompt="Return exactly the requested sentinel and nothing else.",
        user_message=f"Return exactly: {sentinel}",
        max_tokens=64,
        model=model,
        timeout=90,
        effort="low",
        workload="deterministic_ci_live_smoke",
    )

    assert result["text"].strip() == sentinel
    assert result["served_by"]
    assert result["provider"]
