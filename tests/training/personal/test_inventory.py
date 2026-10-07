from __future__ import annotations

import json

from src.training.personal.inventory import inventory_jsonl


def test_inventory_reports_counts_and_hashes_without_content(tmp_path) -> None:
    secret = "SECRET_INVENTORY_MUST_NOT_APPEAR"
    rows = [
        {"conversations": [{"role": "user", "content": secret}], "metadata": {"content_hash": "same"}},
        {"conversations": [{"role": "user", "content": "other"}], "metadata": {"content_hash": "same", "judge_score": 8}},
    ]
    (tmp_path / "data.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows) + "bad\n", encoding="utf-8")
    report = inventory_jsonl(tmp_path).to_dict()
    assert report["lines"] == 3
    assert report["valid"] == 2
    assert report["invalid"] == 1
    assert report["unique"] == 1
    assert report["duplicates"] == 1
    assert report["judged"] == 1
    assert secret not in json.dumps(report)
