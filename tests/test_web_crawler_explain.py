from __future__ import annotations

import json
from pathlib import Path

from src.tools.web_crawler import WebCrawler


def test_campaign_explain_page_returns_business_fields(tmp_path: Path) -> None:
    crawler = WebCrawler(tmp_path)
    campaign_id = "campaign_explain_test"

    campaign_dir = tmp_path / "campaigns" / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)

    state_payload = {
        "campaign_id": campaign_id,
        "seed_url": "https://example.com",
        "stats": {"pages_crawled_total": 10, "interesting_total": 2},
        "interesting": [
            {
                "url": "https://example.com/pricing",
                "title": "Pricing",
                "score": 8.4,
                "excerpt": "Découvrez nos offres pour PME à Paris. Contact: sales@example.com. Dès 49€.",
                "insights": {
                    "offer_summary": "Découvrez nos offres pour PME.",
                    "audience_summary": "Pour PME.",
                    "location_summary": "Basé à Paris.",
                    "contact_emails": ["sales@example.com"],
                    "contact_phones": ["+33 1 23 45 67 89"],
                    "pricing_signals": ["49€"],
                    "cta_signals": ["contact", "devis"],
                    "key_points": ["Offre B2B", "Contact direct", "Prix affiché"],
                },
            }
        ],
    }
    (campaign_dir / "state.json").write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    result = crawler.campaign_explain_page(campaign_id=campaign_id)

    assert result["success"] is True
    exp = result["explanation"]
    assert exp["what_page_offers"]
    assert exp["target_audience"]
    assert exp["where_or_contact"]["emails"] == ["sales@example.com"]
    assert "49€" in exp["pricing_signals"]
