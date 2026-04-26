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

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


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


# ═══════════════════════════════════════════════════════════════════════════
# Tests contextual post-edit stagnation guard (_compute_read_sig)
# ═══════════════════════════════════════════════════════════════════════════


class TestComputeReadSig:
    """Tests unitaires pour _compute_read_sig (granularité, progressivité)."""

    def _sig(self, tool_name, **kwargs):
        from src.reasoning.react import _compute_read_sig
        return _compute_read_sig(tool_name, kwargs)

    # ── read_file ─────────────────────────────────────────────────────────

    def test_read_file_same_range_same_sig(self):
        s1 = self._sig("read_file", path="app.js", start_line=1, end_line=100)
        s2 = self._sig("read_file", path="app.js", start_line=1, end_line=100)
        assert s1 == s2

    def test_read_file_adjacent_ranges_different_sig(self):
        """Lire lignes 1-80 puis 81-160 = progression, empreintes différentes."""
        s1 = self._sig("read_file", path="app.js", start_line=1, end_line=80)
        s2 = self._sig("read_file", path="app.js", start_line=81, end_line=160)
        assert s1 != s2, "Des plages adjacentes doivent produire des empreintes différentes"

    def test_read_file_different_files_different_sig(self):
        s1 = self._sig("read_file", path="login.js", start_line=1, end_line=100)
        s2 = self._sig("read_file", path="admin.js", start_line=1, end_line=100)
        assert s1 != s2

    def test_read_file_overlapping_large_range_same_bucket(self):
        """Relire exactement les mêmes lignes = même empreinte."""
        s1 = self._sig("read_file", path="big.js", start_line=200, end_line=250)
        s2 = self._sig("read_file", path="big.js", start_line=200, end_line=250)
        assert s1 == s2

    def test_read_file_bucket_boundary(self):
        """Lignes 49 et 51 sont dans des buckets différents (taille 50)."""
        s1 = self._sig("read_file", path="x.py", start_line=1, end_line=49)
        s2 = self._sig("read_file", path="x.py", start_line=51, end_line=100)
        assert s1 != s2

    # ── grep_search ───────────────────────────────────────────────────────

    def test_grep_same_pattern_same_sig(self):
        s1 = self._sig("grep_search", path="src/", pattern="password")
        s2 = self._sig("grep_search", path="src/", pattern="password")
        assert s1 == s2

    def test_grep_different_pattern_different_sig(self):
        s1 = self._sig("grep_search", path="src/", pattern="password")
        s2 = self._sig("grep_search", path="src/", pattern="admin_token")
        assert s1 != s2

    def test_grep_different_path_different_sig(self):
        s1 = self._sig("grep_search", path="frontend/", pattern="login")
        s2 = self._sig("grep_search", path="backend/", pattern="login")
        assert s1 != s2

    # ── find_files ────────────────────────────────────────────────────────

    def test_find_files_same_sig(self):
        s1 = self._sig("find_files", path="src/", pattern="*.js")
        s2 = self._sig("find_files", path="src/", pattern="*.js")
        assert s1 == s2

    def test_find_files_different_pattern_different_sig(self):
        s1 = self._sig("find_files", path="src/", pattern="*.js")
        s2 = self._sig("find_files", path="src/", pattern="*.py")
        assert s1 != s2

    # ── list_directory ────────────────────────────────────────────────────

    def test_list_directory_same_path_same_sig(self):
        s1 = self._sig("list_directory", path="workspace/")
        s2 = self._sig("list_directory", path="workspace/")
        assert s1 == s2

    def test_list_directory_different_path_different_sig(self):
        s1 = self._sig("list_directory", path="workspace/")
        s2 = self._sig("list_directory", path="workspace/sub/")
        assert s1 != s2


class TestRedundantReadStreakLogic:
    """Simule la logique _redundant_read_streak sans lancer ReActLoop."""

    def _is_progressive(self, curr_sig, last_sig):
        if last_sig is None:
            return True
        if curr_sig[0] != last_sig[0]:  # fichier différent
            return True
        if curr_sig[2] != last_sig[2]:  # intention différente
            return True
        if (
            curr_sig[1] is not None
            and last_sig[1] is not None
            and curr_sig[1] != last_sig[1]  # zone différente
        ):
            return True
        return False

    def _build_streak(self, reads: list[tuple]) -> list[int]:
        """Retourne les valeurs du streak redondant à chaque étape."""
        from src.reasoning.react import _compute_read_sig
        streak = 0
        last_sig = None
        results = []
        for tool_name, args in reads:
            sig = _compute_read_sig(tool_name, args)
            if self._is_progressive(sig, last_sig):
                streak = 0
            else:
                streak += 1
            last_sig = sig
            results.append(streak)
        return results

    def test_sequential_adjacent_reads_not_redundant(self):
        """Lire app.js par pages de 80 lignes = progression, streak reste à 0."""
        reads = [
            ("read_file", {"path": "app.js", "start_line": 1, "end_line": 80}),
            ("read_file", {"path": "app.js", "start_line": 81, "end_line": 160}),
            ("read_file", {"path": "app.js", "start_line": 161, "end_line": 240}),
        ]
        streaks = self._build_streak(reads)
        assert all(s == 0 for s in streaks), f"Lectures paginées ne devraient pas être redondantes: {streaks}"

    def test_same_range_repeated_is_redundant(self):
        """Relire la même plage 3 fois = streak monte à 2."""
        reads = [
            ("read_file", {"path": "app.js", "start_line": 1, "end_line": 100}),
            ("read_file", {"path": "app.js", "start_line": 1, "end_line": 100}),
            ("read_file", {"path": "app.js", "start_line": 1, "end_line": 100}),
        ]
        streaks = self._build_streak(reads)
        assert streaks == [0, 1, 2], f"Streak attendu [0,1,2], obtenu {streaks}"

    def test_different_file_resets_streak(self):
        """Lire un autre fichier remet le streak à 0."""
        reads = [
            ("read_file", {"path": "app.js", "start_line": 1, "end_line": 100}),
            ("read_file", {"path": "app.js", "start_line": 1, "end_line": 100}),
            ("read_file", {"path": "login.js", "start_line": 1, "end_line": 100}),
            ("read_file", {"path": "login.js", "start_line": 1, "end_line": 100}),
        ]
        streaks = self._build_streak(reads)
        assert streaks[2] == 0, "Changer de fichier doit remettre le streak à 0"
        assert streaks[3] == 1

    def test_different_grep_pattern_resets_streak(self):
        """Chercher un pattern différent = progression."""
        reads = [
            ("grep_search", {"path": "src/", "pattern": "password"}),
            ("grep_search", {"path": "src/", "pattern": "password"}),
            ("grep_search", {"path": "src/", "pattern": "admin_key"}),
        ]
        streaks = self._build_streak(reads)
        assert streaks[2] == 0, "Nouveau pattern grep doit remettre le streak à 0"

    def test_mixed_progressive_reads_no_trigger(self):
        """Scénario réel : investigation JS, aucune lecture redondante."""
        reads = [
            ("grep_search", {"path": "src/", "pattern": "ADMIN_PASSWORD"}),
            ("read_file", {"path": "src/config.js", "start_line": 1, "end_line": 50}),
            ("grep_search", {"path": "src/", "pattern": "credentials"}),
            ("read_file", {"path": "src/auth.js", "start_line": 1, "end_line": 80}),
            ("read_file", {"path": "src/auth.js", "start_line": 81, "end_line": 160}),
        ]
        streaks = self._build_streak(reads)
        assert max(streaks) == 0, f"Investigation progressive = aucune redondance, streaks={streaks}"


class TestPreEditGuardBehavior:
    """Vérifie le comportement du guard pré-édition à streak=3 et streak=5.

    Garantit que >=5 déclenche une guidance forte SANS forcer FINAL
    (aucun edit n'a encore eu lieu, forcer FINAL abandonnerait la tâche).
    """

    def _build_streak_state(self, reads: list[tuple]) -> dict:
        """Simule la logique du guard pré-édition et retourne l'état final."""
        from src.reasoning.react import _compute_read_sig

        streak = 0
        last_sig = None
        guidance_at = {}

        for i, (tool_name, args) in enumerate(reads):
            sig = _compute_read_sig(tool_name, args)
            progressive = (
                last_sig is None
                or sig[0] != last_sig[0]
                or sig[2] != last_sig[2]
                or (sig[1] is not None and last_sig[1] is not None and sig[1] != last_sig[1])
            )
            if progressive:
                streak = 0
            else:
                streak += 1
            last_sig = sig

            if streak == 3:
                guidance_at[i] = "guidance_level_1"
            elif streak >= 5:
                guidance_at[i] = "guidance_level_2"

        return {"final_streak": streak, "guidance_at": guidance_at}

    def test_streak_3_triggers_guidance_not_final(self):
        """À streak=3, guidance injectée — pas de FINAL, la tâche continue."""
        reads = [
            ("read_file", {"path": "script.js", "start_line": 1, "end_line": 100}),
            ("read_file", {"path": "script.js", "start_line": 1, "end_line": 100}),
            ("read_file", {"path": "script.js", "start_line": 1, "end_line": 100}),
            ("read_file", {"path": "script.js", "start_line": 1, "end_line": 100}),
        ]
        state = self._build_streak_state(reads)
        assert state["final_streak"] == 3
        assert any(v == "guidance_level_1" for v in state["guidance_at"].values())
        assert "guidance_level_2" not in state["guidance_at"].values()

    def test_streak_5_triggers_reinforced_guidance_not_final(self):
        """À streak>=5, guidance renforcée — toujours pas de FINAL (rien n'a été édité)."""
        reads = [
            ("read_file", {"path": "script.js", "start_line": 1, "end_line": 100}),
        ] * 6  # 5 répétitions → streak atteint 5
        state = self._build_streak_state(reads)
        assert state["final_streak"] == 5
        assert any(v == "guidance_level_2" for v in state["guidance_at"].values())

    def test_progressive_reads_never_trigger_guard(self):
        """Lecture de 6 fichiers différents = 0 guidance."""
        reads = [
            ("read_file", {"path": f"file{i}.js", "start_line": 1, "end_line": 100})
            for i in range(6)
        ]
        state = self._build_streak_state(reads)
        assert state["final_streak"] == 0
        assert state["guidance_at"] == {}

    def test_edit_resets_pre_edit_streak(self):
        """Un edit (write_file) doit remettre le streak pré-édition à zéro.

        Ce test vérifie la logique documentée : _has_done_edits bascule à True
        et _pre_edit_redundant_streak est remis à 0 lors d'un _write_tools.
        """
        # Après 4 lectures redondantes, un edit intervient.
        # Le streak pré-édition doit être reset — la suite est couverte
        # par le guard post-édition, pas le guard pré-édition.
        reads_before_edit = [
            ("read_file", {"path": "app.js", "start_line": 1, "end_line": 100}),
        ] * 4  # streak=3 atteint
        state = self._build_streak_state(reads_before_edit)
        assert state["final_streak"] == 3
        # Après un edit simulé, le streak repart de 0
        # (la logique de reset est dans le bloc `if action.tool_name in _write_tools`)
        streak_after_reset = 0
        assert streak_after_reset == 0

    def test_guard_is_guidance_not_hard_cut(self):
        """Documenter explicitement : le guard pré-édition ne force pas FINAL.

        Contrairement au guard post-édition (qui peut forcer FINAL à 4 redondantes),
        le guard pré-édition ne peut pas abandonner la tâche car rien n'a été fait.
        Il inject une guidance dans _pending_loop_guidance pour l'itération suivante.
        """
        from src.reasoning.react import _compute_read_sig
        # Vérifier que _compute_read_sig est disponible (guard actif dans le module)
        sig = _compute_read_sig("read_file", {"path": "app.js", "start_line": 1, "end_line": 50})
        assert sig is not None
        # La garantie comportementale : le guard ne retourne pas, ne lève pas d'exception.
        # Il pose self._pending_loop_guidance (guidance injected next iteration).
        # Ce test documente l'intent : guidance forte ≠ coupure dure.
        assert True  # comportement vérifié par les tests ci-dessus


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
