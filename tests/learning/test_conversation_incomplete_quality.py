import json

from src.learning import conversation_logger
from src.training.data_prep import load_lumena_pool


def test_incomplete_react_run_is_flagged_and_excluded_from_training(tmp_path, monkeypatch):
    pool = tmp_path / "pool"
    validated = tmp_path / "validated"
    monkeypatch.setattr(conversation_logger, "_POOL_DIR", pool)
    monkeypatch.setattr(conversation_logger, "_rotation_done", True)

    assert conversation_logger.queue_conversation(
        "Construis le jeu complet avec tous les systèmes demandés",
        "J'ai atteint la limite configurée et la tâche reste incomplète.",
        react_meta={"agent_output_incomplete": True},
    )
    entry = json.loads(next(pool.glob("*.jsonl")).read_text(encoding="utf-8"))
    assert entry["metadata"]["quality_flag"] == "incomplete"
    assert entry["metadata"]["react_meta"]["incomplete"] is True
    assert load_lumena_pool(pool, validated, min_conversations=0) == []
