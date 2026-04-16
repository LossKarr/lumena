from __future__ import annotations

import json
from pathlib import Path

from src.tools.web_crawler import WebCrawler


def test_campaign_generate_pro_report_builds_outputs(tmp_path: Path) -> None:
    crawler = WebCrawler(tmp_path)
    campaign_id = "campaign_test_pro"

    campaign_dir = tmp_path / "campaigns" / campaign_id
    runs_dir = campaign_dir / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    state_payload = {
        "campaign_id": campaign_id,
        "created_at": "2026-02-16T10:00:00Z",
        "updated_at": "2026-02-16T10:05:00Z",
        "seed_url": "https://example.com",
        "seed_domain": "example.com",
        "options": {
            "same_domain_only": True,
            "max_depth": 3,
            "keyword_hint": "pricing, product",
        },
        "limits": {
            "max_total_pages": 100,
        },
        "stats": {
            "runs": 2,
            "pages_crawled_total": 20,
            "errors_total": 3,
            "interesting_total": 7,
        },
        "queue": [
            {"url": "https://example.com/blog", "depth": 1},
        ],
        "visited": [
            "https://example.com",
        ],
        "interesting": [
            {
                "url": "https://example.com/pricing",
                "title": "Pricing",
                "score": 7.2,
                "last_seen": "2026-02-16T10:03:00Z",
                "excerpt": "Plans and pricing details",
            },
            {
                "url": "https://example.com/product",
                "title": "Product",
                "score": 6.1,
                "last_seen": "2026-02-16T10:04:00Z",
                "excerpt": "Core product capabilities",
            },
        ],
        "last_run": {
            "run_id": "run_20260216_100400",
        },
    }
    (campaign_dir / "state.json").write_text(json.dumps(state_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    run_payload = {
        "run_id": "run_20260216_100400",
        "campaign_id": campaign_id,
        "started_at": "2026-02-16T10:04:00Z",
        "finished_at": "2026-02-16T10:05:00Z",
        "duration_sec": 60,
        "stats": {"visited": 10, "interesting": 4, "errors": 2},
        "pages": [
            {"url": "https://example.com/1", "error": "timeout"},
            {"url": "https://example.com/2", "error": "404 not found"},
            {"url": "https://example.com/3", "error": "content-type ignoré"},
            {"url": "https://example.com/4", "error": ""},
        ],
    }
    (runs_dir / "run_20260216_100400.json").write_text(
        json.dumps(run_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result = crawler.campaign_generate_pro_report(
        campaign_id=campaign_id,
        top_n_findings=20,
        include_last_runs=3,
        report_title="Rapport test premium",
    )

    assert result["success"] is True
    assert result["campaign_id"] == campaign_id
    assert float(result["overall_score"]) >= 0.0

    report_json = Path(result["report_json"])
    report_md = Path(result["report_md"])

    assert report_json.exists()
    assert report_md.exists()

    payload = json.loads(report_json.read_text(encoding="utf-8"))
    assert payload["executive"]["campaign_id"] == campaign_id
    assert "scores" in payload["executive"]
    assert len(payload["top_findings"]) >= 1

    markdown = report_md.read_text(encoding="utf-8")
    assert "Résumé exécutif" in markdown
    assert "Rapport test premium" in markdown
