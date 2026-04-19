"""Tests pour les 4 garde-fous critiques (P1-P4) corrigeant la catastrophe CSS.

- P1: Garde-fou écriture destructrice + backup auto
- P2: Invalidation du cache d'observation après N hits
- P4: Auto-quote de `start` sous Windows pour paths avec espaces
(P3 anti-stagnation est couvert par test_react_antistagnation.py)
"""
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.reasoning.react import ToolRegistry


# ──────────────────────────────────────────────────────────────────────
# P1: Garde-fou écriture destructrice
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_p1_write_file_refuses_massive_reduction(tmp_path: Path, monkeypatch):
    """write_file doit refuser une réduction >60% sans force_rewrite."""
    monkeypatch.setenv("LUMENA_PATCH_STRICT", "0")
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    # Simule fichier volumineux (1392 lignes → 212 lignes du bug réel)
    big_content = "\n".join(f".cls{i} {{ color: red; }}" for i in range(1400))
    obs1 = await registry.execute("write_file", {
        "path": "style.css",
        "content": big_content,
    })
    assert obs1.success

    # Tentative de réécriture drastique (bloquée)
    tiny_content = ".cls0 { color: red; }\n" * 5  # ~100 chars vs ~30k
    obs2 = await registry.execute("write_file", {
        "path": "style.css",
        "content": tiny_content,
    })
    assert "refus" in obs2.content.lower() or "destruct" in obs2.content.lower()
    # Fichier original intact
    target = registry.file_guardrails.find_workspace_match(Path("style.css"))
    assert target.read_text(encoding="utf-8") == big_content


@pytest.mark.asyncio
async def test_p1_write_file_allows_reduction_with_force_and_reason(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LUMENA_PATCH_STRICT", "0")
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    big_content = "X" * 5000
    await registry.execute("write_file", {"path": "f.txt", "content": big_content})

    obs = await registry.execute("write_file", {
        "path": "f.txt",
        "content": "tiny",
        "force_rewrite": True,
        "rewrite_reason": "Réécriture complète volontaire (test)",
    })
    assert obs.success
    target = registry.file_guardrails.find_workspace_match(Path("f.txt"))
    assert target.read_text(encoding="utf-8") == "tiny"


@pytest.mark.asyncio
async def test_p1_write_file_creates_backup_on_overwrite(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LUMENA_PATCH_STRICT", "0")
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    original = "A" * 2000
    await registry.execute("write_file", {"path": "bk.txt", "content": original})
    # Écriture non-destructive (taille similaire) → backup attendu
    await registry.execute("write_file", {
        "path": "bk.txt",
        "content": "B" * 2000,
    })
    target = registry.file_guardrails.find_workspace_match(Path("bk.txt"))
    backup_dir = target.parent / ".backups"
    assert backup_dir.exists()
    backups = list(backup_dir.glob("bk.txt.*"))
    assert len(backups) >= 1
    assert backups[0].read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_p1_write_file_small_files_bypass_guard(tmp_path: Path, monkeypatch):
    """Fichiers <400 chars : pas de guard (évite faux-positifs)."""
    monkeypatch.setenv("LUMENA_PATCH_STRICT", "0")
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    await registry.execute("write_file", {"path": "s.txt", "content": "hello world"})
    obs = await registry.execute("write_file", {"path": "s.txt", "content": "hi"})
    assert obs.success


# ──────────────────────────────────────────────────────────────────────
# P2: Cache anti-répétition
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_p2_cache_invalidates_after_max_hits(tmp_path: Path):
    """Le cache doit s'auto-invalider après MAX_HITS pour forcer relecture fraîche."""
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    (tmp_path / "test.txt").write_text("v1", encoding="utf-8")

    # Premier appel : miss → peuple le cache
    await registry.execute("read_file", {"path": "test.txt"})
    cache_size_after_first = len(registry._observation_cache)
    assert cache_size_after_first >= 1

    # Identifie la clé
    cache_key = next(k for k in registry._observation_cache if "read_file" in k)

    # Hits répétés : le compteur doit évincer après MAX_HITS
    max_hits = registry._OBS_CACHE_MAX_HITS
    for _ in range(max_hits + 2):
        await registry.execute("read_file", {"path": "test.txt"})

    # Après invalidation, le compteur est remis à 0 et la clé a été repeuplée
    assert registry._observation_cache_hits.get(cache_key, 0) <= max_hits


@pytest.mark.asyncio
async def test_p2_cache_reset_hit_counter_on_repopulation(tmp_path: Path):
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    (tmp_path / "a.txt").write_text("data", encoding="utf-8")
    await registry.execute("read_file", {"path": "a.txt"})
    cache_key = next(k for k in registry._observation_cache if "read_file" in k)
    # Fresh insert → counter must be 0
    assert registry._observation_cache_hits.get(cache_key) == 0


# ──────────────────────────────────────────────────────────────────────
# P4: Auto-quote de `start` Windows
# ──────────────────────────────────────────────────────────────────────

def test_p4_start_path_quoting_regex():
    """Vérifie la regex d'auto-quote pour la commande start."""
    import re
    cmd = r'start C:\Users\a\SITE WEB LUMENA\index.html'
    m = re.match(r'^start\s+(?!"")(.+)$', cmd.strip(), re.IGNORECASE)
    assert m is not None
    arg = m.group(1).strip()
    assert ' ' in arg
    quoted = f'start "" "{arg}"'
    assert quoted == r'start "" "C:\Users\a\SITE WEB LUMENA\index.html"'


def test_p4_start_already_quoted_not_touched():
    import re
    cmd = r'start "" "C:\Path With Spaces\file.html"'
    # Le regex `(?!"")` exclut ce cas
    m = re.match(r'^start\s+(?!"")(.+)$', cmd.strip(), re.IGNORECASE)
    assert m is None


# ──────────────────────────────────────────────────────────────────────
# P3: Anti-stagnation HARD (force FINAL si 5× actions identiques)
# ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_p3_stagnation_hard_forces_final_on_identical_actions(tmp_path: Path):
    """5 pensées + 5 actions identiques → FORCE FINAL synthétique."""
    from src.reasoning.react import ReActLoop

    call_count = 0

    async def _mock_llm(_messages, **kwargs):
        nonlocal call_count
        call_count += 1
        # Même pensée, même action, même args : vraie stagnation
        return (
            "THOUGHT: Je vérifie encore le fichier pour être sûr.\n"
            "ACTION: read_file\n"
            'ACTION_INPUT: {"path": "test.py"}'
        )

    (tmp_path / "test.py").write_text("x = 1\n", encoding="utf-8")
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    loop = ReActLoop(llm_chat_func=_mock_llm, tools=registry)
    loop.max_iterations = 15

    result = await loop.run("analyse test.py")
    # Doit s'être arrêté avant max_iterations (grâce au P3 OU à un guard antérieur)
    assert len(loop.history) < 15, (
        f"La boucle a tourné {len(loop.history)}× malgré stagnation totale"
    )

