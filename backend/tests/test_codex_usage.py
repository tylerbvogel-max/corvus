from app.services.codex_usage import _format_report


def test_format_report_exposes_limits_and_tokens_without_guessing_cash():
    report = _format_report({
        "rateLimits": {"planType": "prolite"},
        "rateLimitsByLimitId": {
            "codex": {"limitName": None, "primary": {
                "usedPercent": 11, "windowDurationMins": 10080, "resetsAt": 1784668103}},
            "codex_bengalfox": {"limitName": "GPT Spark", "primary": {
                "usedPercent": 75, "windowDurationMins": 10080, "resetsAt": 1784743954}},
        },
        "rateLimitResetCredits": {"availableCount": 0},
    }, {"summary": {"lifetimeTokens": 123}, "dailyUsageBuckets": []}, 1_700_000_000)
    assert report["available"] is True
    assert report["summary"]["lifetimeTokens"] == 123
    assert report["gauges"][0]["label"] == "Codex · 1-week window"
    assert report["gauges"][1]["severity"] == "warning"
    assert "cash" not in report
