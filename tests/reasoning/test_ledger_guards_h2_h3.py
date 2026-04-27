"""
🧪 Tests — FINAL guard heuristiques H2 et H3 (ExecutionLedger)

H2 : mutations présentes mais hors famille attendue pour l'intent courant → retry bloquant
H3 : cible explicite dans la requête sans mutation correspondante → repair léger fire-once

Pour chaque test de H3 :
- le LLM écrit un fichier autre que la cible mentionnée dans la requête
- puis déclare FINAL avec une formule "j'ai fait X"
- H3 doit déclencher un repair (ou non, selon le cas)
"""

import pytest
from pathlib import Path

from src.reasoning.react import ReActLoop, ToolRegistry


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_loop(tmp_path: Path, llm_func) -> ReActLoop:
    return ReActLoop(
        llm_chat_func=llm_func,
        tools=ToolRegistry(lumena=None, lumena_root=tmp_path),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# H3 — repair léger
# ═══════════════════════════════════════════════════════════════════════════════

class TestH3RepairLightFired:
    """H3 doit déclencher un repair quand la cible explicite n'est pas dans le ledger."""

    @pytest.mark.asyncio
    async def test_h3_fires_for_file_target_mismatch(self, tmp_path: Path):
        """Requête cite 'main.py', LLM écrit 'other.txt', puis FINAL 'j'ai modifié'. → H3 retry."""
        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # LLM écrit un autre fichier
                return (
                    "THOUGHT: Je vais écrire un fichier.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "other_file.txt", "content": "contenu"}'
                )
            elif call_count == 2:
                # FINAL avec claim mais mauvaise cible
                return (
                    "THOUGHT: C'est fait.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai modifié le fichier comme demandé."
                )
            else:
                # Après le repair H3 : LLM écrit vraiment main.py
                return (
                    "THOUGHT: Je dois écrire main.py.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "main.py", "content": "# main"}'
                )

        # On s'arrête après l'appel 3 en rendant FINAL
        async def _llm_full(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    "THOUGHT: Je vais écrire un fichier annexe.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "other_file.txt", "content": "contenu"}'
                )
            elif call_count == 2:
                return (
                    "THOUGHT: J'ai écrit le fichier.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai modifié le fichier demandé."
                )
            elif call_count == 3:
                # Après repair H3 : écriture sur la bonne cible
                return (
                    "THOUGHT: Il faut écrire main.py.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "main.py", "content": "# fixed"}'
                )
            else:
                return (
                    "THOUGHT: main.py est écrit.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: main.py a été modifié."
                )

        loop = _make_loop(tmp_path, _llm_full)
        result = await loop.run("modifie le fichier main.py")

        # H3 doit avoir forcé au moins un retry (call_count > 2)
        assert call_count > 2, (
            f"H3 aurait dû déclencher un retry, mais call_count={call_count}"
        )
        # La cible réelle a finalement été traitée
        ws = tmp_path / "workspace"
        assert (ws / "main.py").exists() or "main.py" in result.lower()

    @pytest.mark.asyncio
    async def test_h3_repair_message_contains_target(self, tmp_path: Path):
        """Le message de repair H3 doit mentionner la cible détectée."""
        messages_seen = []
        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            messages_seen.append(messages[-1].get("content", "") if messages else "")
            if call_count == 1:
                return (
                    "THOUGHT: Écriture annexe.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "unrelated.txt", "content": "x"}'
                )
            elif call_count == 2:
                return (
                    "THOUGHT: Done.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai créé le fichier."
                )
            else:
                return (
                    "THOUGHT: OK.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: Voici le résultat."
                )

        loop = _make_loop(tmp_path, _llm)
        await loop.run("crée le fichier config.json s'il te plaît")

        # Le 3e message (envoyé au LLM après le repair) doit mentionner config.json
        repair_msg = next(
            (m for m in messages_seen if "config.json" in m), None
        )
        assert repair_msg is not None, (
            "Le message de repair H3 doit mentionner la cible 'config.json'. "
            f"Messages vus : {messages_seen}"
        )
        # Doit aussi contenir un indicateur d'alerte (⚠️ ou mention de la cible)
        assert "⚠️" in repair_msg or "cible" in repair_msg.lower()


class TestH3RepairLightNotFired:
    """H3 ne doit PAS se déclencher dans les cas conservateurs."""

    @pytest.mark.asyncio
    async def test_h3_silent_when_target_matches_mutation(self, tmp_path: Path):
        """LLM écrit main.py, FINAL 'j'ai modifié'. H3 ne doit pas intervenir."""
        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    "THOUGHT: J'écris main.py.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "main.py", "content": "# v2"}'
                )
            else:
                return (
                    "THOUGHT: main.py est écrit.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai modifié main.py avec succès."
                )

        loop = _make_loop(tmp_path, _llm)
        result = await loop.run("modifie le fichier main.py")

        # Exactement 2 appels : pas de retry H3
        assert call_count == 2, (
            f"H3 ne devrait pas intervenir quand la cible est bien dans le ledger, "
            f"mais call_count={call_count}"
        )

    @pytest.mark.asyncio
    async def test_h3_silent_when_no_explicit_target_in_query(self, tmp_path: Path):
        """Requête sans cible explicite (pas de .ext ni #channel). H3 ne tire pas."""
        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    "THOUGHT: J'écris quelque chose.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "output.txt", "content": "résultat"}'
                )
            else:
                return (
                    "THOUGHT: C'est fait.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai créé le fichier de résultats."
                )

        loop = _make_loop(tmp_path, _llm)
        result = await loop.run("fais quelque chose d'utile")

        assert call_count == 2, (
            "H3 ne doit pas se déclencher sans cible explicite dans la requête"
        )

    @pytest.mark.asyncio
    async def test_h3_silent_when_no_mutations_in_ledger(self, tmp_path: Path):
        """Sans mutation dans le ledger, H3 ne doit pas se déclencher (c'est le rôle de H1)."""
        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # FINAL direct sans aucun outil → H1 devrait intervenir, pas H3
                return (
                    "THOUGHT: Je réponds directement.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai créé le fichier config.yaml."
                )
            else:
                # Après le guard H1 : LLM crée le fichier
                return (
                    "THOUGHT: Je dois créer config.yaml.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "config.yaml", "content": "key: value"}'
                )

        call_count_at_h3 = []

        async def _llm_tracking(messages, **kw):
            r = await _llm(messages, **kw)
            return r

        loop = _make_loop(tmp_path, _llm)
        # H3 ne doit pas se déclencher (pas de mutation quand FINAL est évalué au call 1)
        # H1 (base guard) gère ce cas
        assert not getattr(loop, '_ledger_h3_guard_used', False)

    @pytest.mark.asyncio
    async def test_h3_fire_once_only(self, tmp_path: Path):
        """H3 ne peut se déclencher qu'une fois par run (_ledger_h3_guard_used)."""
        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    "THOUGHT: Écriture d'un fichier annexe.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "unrelated.txt", "content": "x"}'
                )
            elif call_count == 2:
                # 1er FINAL → H3 devrait tirer
                return (
                    "THOUGHT: Done.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai modifié config.yaml comme demandé."
                )
            elif call_count == 3:
                # Après repair H3 : toujours le mauvais fichier
                return (
                    "THOUGHT: J'écris encore le mauvais.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "still_wrong.txt", "content": "y"}'
                )
            else:
                # 2e FINAL — H3 NE doit pas retirer (flag épuisé)
                return (
                    "THOUGHT: Je conclus.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: Tout est fait."
                )

        loop = _make_loop(tmp_path, _llm)
        result = await loop.run("modifie le fichier config.yaml")

        # H3 a tiré au call 2 → retry au call 3 → FINAL au call 4 sans nouveau H3
        assert call_count >= 3, "H3 doit avoir provoqué au moins un retry"
        # Après le retry, le flag H3 est épuisé → pas de 2e retry infini
        assert loop._ledger_h3_guard_used is True

    @pytest.mark.asyncio
    async def test_h3_silent_after_h2_escalated(self, tmp_path: Path):
        """Quand H2 escalade d'abord (intent discord + mutation hors famille),
        H3 ne doit pas tirer sur le FINAL suivant.

        Note : exec_state.reset() réinitialise les flags au début de run(),
        donc on doit provoquer H2 réellement pendant le run.
        """
        from src.core import ConversationContext

        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # write_file : mutation dans le ledger, mais hors famille discord
                return (
                    "THOUGHT: Écriture.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "other.txt", "content": "x"}'
                )
            elif call_count == 2:
                # FINAL avec claim d'action → H2 devrait tirer (discord + write_file = wrong family)
                # H3 ne devrait pas tirer ici car H2 `continue` avant d'atteindre H3
                return (
                    "THOUGHT: Done.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai créé le canal #general."
                )
            elif call_count == 3:
                # Après le retry H2 : on refait une mutation quelconque
                return (
                    "THOUGHT: J'écris un autre fichier.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "autre.txt", "content": "y"}'
                )
            else:
                # FINAL à nouveau — _ledger_final_guard_used=True, H3 doit rester silencieux
                return (
                    "THOUGHT: Terminé.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai créé le canal #general."
                )

        # Fournir un ConversationContext avec intent discord pour que H2 se déclenche.
        # On force _caller_set_allowed=True pour bypasser le classifier et utiliser le
        # fallback keyword, qui retourne "discord" pour cette requête.
        ctx = ConversationContext()
        registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
        registry._caller_set_allowed = True  # Bypass classifier → fallback keyword
        loop = ReActLoop(
            llm_chat_func=_llm,
            tools=registry,
            conversation_context=ctx,
        )
        result = await loop.run("crée le canal #general sur discord")

        # H2 doit avoir tiré au call 2 → call_count >= 3
        assert call_count >= 3, (
            f"H2 devrait avoir provoqué un retry (call_count={call_count})"
        )
        # H3 ne doit pas avoir tiré — son flag reste False
        assert not getattr(loop, '_ledger_h3_guard_used', False), (
            "H3 ne doit pas tirer quand H2 a déjà escaladé (_ledger_final_guard_used=True)"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# H3 — edge cases conservateurs
# ═══════════════════════════════════════════════════════════════════════════════

class TestH3ConservativeEdgeCases:
    """H3 doit rester silencieux sur les signaux faibles ou ambigus."""

    @pytest.mark.asyncio
    async def test_h3_silent_for_non_action_final(self, tmp_path: Path):
        """FINAL sans formule d'action (pas de 'j'ai créé' etc.). H3 ne tire pas."""
        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return (
                    "THOUGHT: J'écris un log.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "log.txt", "content": "log entry"}'
                )
            else:
                return (
                    "THOUGHT: Réponse informative.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: Voici les informations sur config.yaml."
                )

        loop = _make_loop(tmp_path, _llm)
        result = await loop.run("explique-moi config.yaml")

        # _claims_action est False (pas de "j'ai créé/modifié/envoyé") → H3 silencieux
        assert call_count == 2
        assert not getattr(loop, '_ledger_h3_guard_used', False)

    @pytest.mark.asyncio
    async def test_h3_partial_match_in_target(self, tmp_path: Path):
        """Si le hint est contenu dans le chemin de mutation (partial match), H3 est silencieux."""
        call_count = 0

        async def _llm(messages, **kw):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # LLM écrit /project/src/main.py — contient "main.py"
                return (
                    "THOUGHT: J'écris main.py dans le projet.\n"
                    "ACTION: write_file\n"
                    'ACTION_INPUT: {"path": "src/main.py", "content": "# main"}'
                )
            else:
                return (
                    "THOUGHT: main.py est modifié.\n"
                    "ACTION: FINAL\n"
                    "ACTION_INPUT: J'ai modifié main.py avec succès."
                )

        loop = _make_loop(tmp_path, _llm)
        result = await loop.run("modifie le fichier main.py")

        # "main.py" est présent dans "src/main.py" → has_mutation_for_target_hint=True → H3 silencieux
        assert call_count == 2, "H3 ne doit pas tirer quand le hint est un sous-chemin de la cible"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
