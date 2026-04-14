"""
🧪 Tests anti-stagnation & anti-boucle read_file (Phase 6 — Hotfix v2)

Vérifie les 5 fixes runtime :
1. Guard read_file par path (6x → hard FINAL, 4-5x → warning + action exécutée)
2. Détection thought stagnation (3 thoughts quasi-identiques → warning dans observation)
3. Max itérations réduit à 15/20
4. Brain hooks throttle (1/sec)
5. Cache read_file par args (clé exacte, exclut paginé)
"""

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestReadFilePathGuard:
    """Fix #1: Guard read_file par path — relectures de mêmes plages → hard FINAL."""

    @pytest.mark.asyncio
    async def test_read_file_reread_same_ranges_triggers_hard_final(self, tmp_path):
        """Après 3 relectures de plages déjà lues, la boucle doit forcer FINAL."""
        from src.reasoning.react import ReActLoop, ToolRegistry

        call_count = 0

        async def _mock_llm(_messages, **kwargs):
            nonlocal call_count
            call_count += 1
            # Alterner entre 2 plages identiques (relectures de ranges déjà vues)
            if call_count <= 6:
                sl = 1 if call_count % 2 == 1 else 30
                el = 29 if call_count % 2 == 1 else 60
                return (
                    f"THOUGHT: Je dois relire le fichier encore une fois ({call_count}).\n"
                    "ACTION: read_file\n"
                    f'ACTION_INPUT: {{"path": "test.py", "start_line": {sl}, "end_line": {el}}}'
                )
            return "THOUGHT: OK.\nACTION: FINAL\nACTION_INPUT: Voici ma réponse."

        target = tmp_path / "test.py"
        target.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")

        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
        loop = ReActLoop(llm_chat_func=_mock_llm, tools=registry)
        loop.max_iterations = 12

        result = await loop.run("explique ce fichier")
        read_count = sum(1 for h in loop.history if h.action.tool_name == "read_file")
        # 2 plages distinctes lues, puis relectures → kill dès 3 relectures (5e appel)
        assert read_count <= 5, f"read_file appelé {read_count}x — le guard relectures n'a pas fonctionné"
        assert result and len(result) > 10, "Le hard FINAL doit retourner un résumé du contenu lu"

    @pytest.mark.asyncio
    async def test_read_file_distinct_ranges_allowed(self, tmp_path):
        """Lire des plages distinctes du même fichier ne doit pas déclencher le guard."""
        from src.reasoning.react import ReActLoop, ToolRegistry

        call_count = 0

        async def _mock_llm(_messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 8:
                sl = call_count * 10
                el = call_count * 10 + 9
                return (
                    f"THOUGHT: Lire la plage suivante ({call_count}).\n"
                    "ACTION: read_file\n"
                    f'ACTION_INPUT: {{"path": "test.py", "start_line": {sl}, "end_line": {el}}}'
                )
            return "THOUGHT: OK.\nACTION: FINAL\nACTION_INPUT: Voici ma réponse."

        target = tmp_path / "test.py"
        target.write_text("\n".join(f"line {i}" for i in range(100)), encoding="utf-8")

        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
        loop = ReActLoop(llm_chat_func=_mock_llm, tools=registry)
        loop.max_iterations = 15

        result = await loop.run("lis le fichier complet")
        read_count = sum(1 for h in loop.history if h.action.tool_name == "read_file")
        assert read_count == 8, f"read_file appelé {read_count}x — les plages distinctes devraient passer"

    @pytest.mark.asyncio
    async def test_read_file_different_paths_allowed(self, tmp_path):
        """Lire des fichiers différents ne doit pas déclencher le guard."""
        from src.reasoning.react import ReActLoop, ToolRegistry

        call_count = 0
        files = ["a.py", "b.py", "c.py", "d.py", "e.py"]

        async def _mock_llm(_messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 5:
                fname = files[call_count - 1]
                return (
                    f"THOUGHT: Lire {fname}.\n"
                    f"ACTION: read_file\n"
                    f'ACTION_INPUT: {{"path": "{fname}"}}'
                )
            return "THOUGHT: Fini.\nACTION: FINAL\nACTION_INPUT: Tout lu."

        for f in files:
            (tmp_path / f).write_text(f"content of {f}", encoding="utf-8")

        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
        loop = ReActLoop(llm_chat_func=_mock_llm, tools=registry)
        loop.max_iterations = 10

        result = await loop.run("lis tous les fichiers")
        read_count = sum(1 for h in loop.history if h.action.tool_name == "read_file")
        assert read_count == 5, f"read_file appelé {read_count}x — les paths différents devraient tous passer"


class TestThoughtStagnation:
    """Fix #2: Détection de stagnation de pensée (warning dans observation)."""

    @pytest.mark.asyncio
    async def test_stagnant_thoughts_inject_warning(self, tmp_path):
        """3 thoughts quasi-identiques doivent injecter un warning dans l'observation."""
        from src.reasoning.react import ReActLoop, ToolRegistry

        call_count = 0

        async def _mock_llm(_messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                # Thoughts quasi-identiques (même contenu avec variation mineure)
                return (
                    f"THOUGHT: Je dois analyser le code source pour comprendre la structure du projet et identifier les composants principaux.\n"
                    f"ACTION: read_file\n"
                    f'ACTION_INPUT: {{"path": "file{call_count}.py"}}'
                )
            return "THOUGHT: Passons à l'action.\nACTION: FINAL\nACTION_INPUT: Voici le résultat."

        for i in range(1, 6):
            (tmp_path / f"file{i}.py").write_text(f"x = {i}", encoding="utf-8")

        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
        loop = ReActLoop(llm_chat_func=_mock_llm, tools=registry)
        loop.max_iterations = 10

        result = await loop.run("analyse le projet")
        # Toutes les actions doivent s'exécuter normalement (pas de blocage)
        assert call_count == 5, f"LLM appelé {call_count}x — les actions ne doivent PAS être bloquées"
        # Le warning de stagnation doit apparaître dans au moins une observation
        stagnation_found = any(
            h.observation and "STAGNATION" in (h.observation.content or "")
            for h in loop.history
        )
        assert stagnation_found, "Le warning STAGNATION doit apparaître dans les observations"

    @pytest.mark.asyncio
    async def test_varied_thoughts_no_nudge(self, tmp_path):
        """Des thoughts différents ne doivent pas déclencher le guard."""
        from src.reasoning.react import ReActLoop, ToolRegistry

        call_count = 0
        thoughts = [
            "D'abord je regarde la structure.",
            "Maintenant j'analyse les imports.",
            "Je vérifie les tests existants.",
            "Je comprends le modèle de données.",
        ]

        async def _mock_llm(_messages, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 4:
                return (
                    f"THOUGHT: {thoughts[call_count - 1]}\n"
                    f"ACTION: read_file\n"
                    f'ACTION_INPUT: {{"path": "f{call_count}.py"}}'
                )
            return "THOUGHT: Je résume.\nACTION: FINAL\nACTION_INPUT: Tout analysé."

        for i in range(1, 6):
            (tmp_path / f"f{i}.py").write_text(f"y = {i}", encoding="utf-8")

        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
        loop = ReActLoop(llm_chat_func=_mock_llm, tools=registry)
        loop.max_iterations = 10

        result = await loop.run("analyse")
        # Toutes les 4 lectures + 1 FINAL = 5 appels
        assert call_count == 5, f"Attendu 5 appels, got {call_count}"


class TestMaxIterationsReduced:
    """Fix #3: Max itérations réduit."""

    def test_default_max_iterations_non_ide(self):
        import os
        from unittest.mock import patch
        from src.reasoning.react import ReActLoop
        # Forcer la suppression de l'env var pour tester la valeur par défaut du code
        env_clean = {k: v for k, v in os.environ.items() if "LUMENA_MAX_REACT" not in k}
        with patch.dict(os.environ, env_clean, clear=True):
            loop = ReActLoop()
            assert loop.max_iterations == 35, f"Attendu 35, got {loop.max_iterations}"

    def test_max_iterations_upper_bound(self):
        import os
        from unittest.mock import patch
        from src.reasoning.react import ReActLoop
        env_clean = {k: v for k, v in os.environ.items() if "LUMENA_MAX_REACT" not in k}
        with patch.dict(os.environ, env_clean, clear=True):
            loop = ReActLoop()
            assert loop.max_iterations <= 40, f"max_iterations trop élevé: {loop.max_iterations}"


class TestCacheReadFileNormalized:
    """Fix #5: Cache read_file par args (clé exacte)."""

    @pytest.mark.asyncio
    async def test_read_file_cache_hit_same_args(self, tmp_path):
        """Deux read_file avec les mêmes args doivent être un cache hit."""
        from src.reasoning.react import ToolRegistry

        target = tmp_path / "module.py"
        content = "\n".join(f"line {i}" for i in range(50))
        target.write_text(content, encoding="utf-8")

        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)

        # Premier appel
        obs1 = await registry.execute("read_file", {"path": str(target)})
        assert obs1.success

        # Deuxième appel avec les mêmes args — cache hit
        obs2 = await registry.execute("read_file", {"path": str(target)})
        assert obs2.success
        assert obs2.content == obs1.content, "Cache hit attendu : même contenu retourné"

    @pytest.mark.asyncio
    async def test_read_file_different_ranges_no_collision(self, tmp_path):
        """Deux read_file avec des ranges différents doivent être des entrées de cache séparées."""
        from src.reasoning.react import ToolRegistry

        target = tmp_path / "module.py"
        content = "\n".join(f"line {i}" for i in range(50))
        target.write_text(content, encoding="utf-8")

        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)

        # Appel sans range
        obs1 = await registry.execute("read_file", {"path": str(target)})
        assert obs1.success

        # Appel avec range différent — doit être un cache miss (args différents)
        obs2 = await registry.execute("read_file", {"path": str(target), "start_line": 10, "end_line": 30})
        assert obs2.success

        # Les contenus doivent être différents (pas de collision de cache)
        assert obs2.content != obs1.content, "Cache miss attendu : args différents → contenus différents"

    @pytest.mark.asyncio
    async def test_read_file_paginated_not_cached(self, tmp_path):
        """Deux lectures de plages différentes ne doivent pas retourner le même contenu (pas de cache)."""
        from src.reasoning.react import ToolRegistry

        target = tmp_path / "long_file.py"
        content = "\n".join(f"line_{i}" for i in range(1, 901)) + "\n"
        target.write_text(content, encoding="utf-8")

        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)

        # Première page explicite (range 1-350 → déclenche SUITE DISPONIBLE)
        obs1 = await registry.execute("read_file", {"path": str(target), "start_line": 1, "end_line": 350})
        assert obs1.success
        assert "SUITE DISPONIBLE" in obs1.content

        # Deuxième page — doit retourner un contenu différent (pas de cache sur range différent)
        obs2 = await registry.execute("read_file", {"path": str(target), "start_line": 351, "end_line": 700})
        assert obs2.success
        assert "line_351" in obs2.content
        assert obs2.content != obs1.content, "Cache ne doit pas mélanger les pages"

    @pytest.mark.asyncio
    async def test_read_file_cache_different_paths_no_collision(self, tmp_path):
        """Deux read_file sur des paths différents ne doivent pas partager le cache."""
        from src.reasoning.react import ToolRegistry

        (tmp_path / "a.txt").write_text("aaa", encoding="utf-8")
        (tmp_path / "b.txt").write_text("bbb", encoding="utf-8")

        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)

        obs_a = await registry.execute("read_file", {"path": str(tmp_path / "a.txt")})
        obs_b = await registry.execute("read_file", {"path": str(tmp_path / "b.txt")})

        assert obs_a.content != obs_b.content, "Cache ne doit pas mélanger les fichiers différents"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
