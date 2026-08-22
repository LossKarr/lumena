from src.reasoning.react import _legal_browser_source_pivot


def _pivot(url, reason, tried=()):
    return _legal_browser_source_pivot(
        url, reason, "trouve une source publique fiable", tried,
    )


def test_cloudflare_pivots_to_other_legal_sources():
    pivot = _pivot(
        "https://blocked.example/listing",
        "protection Cloudflare detectee",
    )

    assert pivot is not None
    origin, guidance = pivot
    assert origin == "https://blocked.example"
    assert "Ne retente pas" in guidance
    assert "web_fetch" in guidance
    assert "API officielle" in guidance
    assert "contourne aucun CAPTCHA/WAF" in guidance


def test_normalized_anti_bot_reason_pivots_to_other_legal_sources():
    assert _pivot(
        "https://blocked.example/protected",
        "anti bot detecte",
    ) is not None


def test_same_blocked_origin_is_not_retried():
    assert _pivot(
        "https://blocked.example/other",
        "challenge Cloudflare actif",
        {"https://blocked.example"},
    ) is None


def test_source_pivot_is_bounded():
    assert _pivot(
        "https://fourth.example",
        "CAPTCHA requis",
        {"https://one.example", "https://two.example", "https://three.example"},
    ) is None


def test_non_antibot_server_error_does_not_open_source_search():
    assert _pivot(
        "https://broken.example",
        "erreur applicative site en panne",
    ) is None
