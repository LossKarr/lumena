"""Voice V2 — SpeechNormalizer (ne jamais lire l'imprononçable)."""
from src.voice.v2 import normalize_for_speech


def test_file_path_not_spelled():
    r = normalize_for_speech("Lis le fichier config.local.php et dis-moi.")
    assert "config.local.php" not in r.spoken
    assert "écran" in r.spoken and "path" in r.suppressed


def test_url_suppressed():
    r = normalize_for_speech("Va sur https://exemple.fr/page?x=1&y=2 maintenant.")
    assert "https://" not in r.spoken and "url" in r.suppressed


def test_long_hash_suppressed():
    h = "8895ae0ec95f97855a5f80a28254cba82284a7da"
    r = normalize_for_speech(f"Le secret webhook est {h}.")
    assert h not in r.spoken and "hash" in r.suppressed


def test_code_block_not_read():
    txt = "Voici le code:\n```python\nfor i in range(10):\n    print(i)\n```\nVoilà."
    r = normalize_for_speech(txt)
    assert "range(10)" not in r.spoken and "code" in r.suppressed
    assert "Voilà." in r.spoken


def test_markdown_table_summarized():
    txt = "Résultats:\n| col1 | col2 |\n|---|---|\n| a | b |\n| c | d |\nFin."
    r = normalize_for_speech(txt)
    assert "| col1 |" not in r.spoken and "table" in r.suppressed
    assert "Fin." in r.spoken


def test_sql_line_suppressed():
    r = normalize_for_speech("Requête:\nSELECT * FROM users WHERE id = 1\nVoilà.")
    assert "SELECT" not in r.spoken and "sql" in r.suppressed


def test_secret_value_never_spoken():
    r = normalize_for_speech("config: db_password=SuperSecret123 et voilà")
    assert "SuperSecret123" not in r.spoken and "secret" in r.suppressed


def test_normal_prose_with_numbers_and_dates_preserved():
    txt = "Tu as 3 factures impayées pour 1250 euros, échéance le 2026-05-26."
    r = normalize_for_speech(txt)
    assert r.spoken == txt          # rien supprimé
    assert r.suppressed == set()
    assert "3 factures" in r.spoken and "2026-05-26" in r.spoken


def test_empty_text():
    r = normalize_for_speech("")
    assert r.spoken == "" and r.suppressed == set()
