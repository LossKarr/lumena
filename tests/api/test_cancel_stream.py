"""Tests — Mécanisme d'annulation serveur du stream SSE.

Vérifie que :
1. Le cancel_event est toujours set dans le finally de generate(),
   même en cas de déconnexion client avant réception du stream_id.
2. L'endpoint /api/chat/cancel fonctionne avec un stream_id valide.
3. Un cancel arrive après que le token a déjà été consommé → réponse gracieuse.
4. Un cancel sans stream_id → erreur propre (pas de crash).
5. Le fallback finally set cancel_event même pour un run normal.
6. Aucun arrêt forcé par ctypes/PyThreadState_SetAsyncExc — annulation uniquement coopérative.
"""

import asyncio
import threading
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import web.routes.chat as _chat_module
from web.routes.chat import _CANCEL_TOKENS


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_stream_id():
    sid = str(uuid.uuid4())
    ev = threading.Event()
    _CANCEL_TOKENS[sid] = ev
    return sid, ev


def _cleanup(sid):
    _CANCEL_TOKENS.pop(sid, None)


# ─────────────────────────────────────────────────────────────────────────────
# cancel_event.set() dans le finally — comportement garanti
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelEventFinallyGuarantee:
    """Vérifie que cancel_event.set() est appelé dans tous les cas de sortie
    d'un generator qui simule generate()."""

    def _make_generator(self, cancel_event: threading.Event, raise_on_yield: Exception = None):
        """Simule la structure try/finally de generate()."""
        try:
            yield "start"
            if raise_on_yield:
                raise raise_on_yield
            yield "stream_id"
            yield "done"
        finally:
            cancel_event.set()  # ← Le fix

    def test_normal_completion_sets_event(self):
        ev = threading.Event()
        gen = self._make_generator(ev)
        list(gen)  # Consume completely
        assert ev.is_set(), "cancel_event doit être set après complétion normale"

    def test_exception_in_generator_sets_event(self):
        ev = threading.Event()
        gen = self._make_generator(ev, raise_on_yield=RuntimeError("network error"))
        with pytest.raises(RuntimeError):
            list(gen)
        assert ev.is_set(), "cancel_event doit être set même après exception"

    def test_generator_close_sets_event(self):
        """Simule la déconnexion client (GeneratorExit via gen.close())."""
        ev = threading.Event()
        gen = self._make_generator(ev)
        next(gen)  # Receive "start"
        gen.close()  # Simulate client disconnect
        assert ev.is_set(), "cancel_event doit être set quand le client se déconnecte"

    def test_generator_close_before_stream_id_sets_event(self):
        """Cas clé : Stop avant réception du stream_id."""
        ev = threading.Event()
        gen = self._make_generator(ev)
        next(gen)  # Receive "start" — stream_id pas encore émis
        # Utilisateur clique Stop → AbortController.abort() → ASGI ferme le generator
        gen.close()
        assert ev.is_set(), "cancel_event doit être set même si stream_id n'a pas été émis"

    def test_event_already_set_before_finally_is_idempotent(self):
        """Si cancel a déjà été demandé via l'endpoint, le finally ne pose pas de problème."""
        ev = threading.Event()
        ev.set()  # Already cancelled via /api/chat/cancel
        gen = self._make_generator(ev)
        gen.close()
        assert ev.is_set()  # Toujours set


# ─────────────────────────────────────────────────────────────────────────────
# _CANCEL_TOKENS — registre d'annulation
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelTokensRegistry:
    def test_register_and_cancel(self):
        sid, ev = _make_stream_id()
        assert sid in _CANCEL_TOKENS
        assert not ev.is_set()
        ev.set()
        assert ev.is_set()
        _cleanup(sid)

    def test_pop_removes_entry(self):
        sid, ev = _make_stream_id()
        _CANCEL_TOKENS.pop(sid, None)
        assert sid not in _CANCEL_TOKENS

    def test_double_pop_safe(self):
        """Appel double de pop ne lève pas d'exception."""
        sid, _ = _make_stream_id()
        _CANCEL_TOKENS.pop(sid, None)
        _CANCEL_TOKENS.pop(sid, None)  # Ne doit pas lever

    def test_unknown_sid_pop_returns_none(self):
        result = _CANCEL_TOKENS.pop("inexistant-sid", None)
        assert result is None

    def test_cancel_event_survives_token_removal(self):
        """Le cancel_event reste valide même après removal du token."""
        sid, ev = _make_stream_id()
        _CANCEL_TOKENS.pop(sid, None)
        ev.set()  # Ne doit pas lever
        assert ev.is_set()


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint /api/chat/cancel — logique métier
# ─────────────────────────────────────────────────────────────────────────────

class TestCancelEndpointLogic:
    """Tests unitaires de la logique cancel_chat_stream sans HTTP."""

    async def _call_cancel(self, body: dict):
        from web.routes.chat import cancel_chat_stream
        class _FakeAuth:
            pass
        return await cancel_chat_stream(body, _FakeAuth())

    def test_valid_stream_id_sets_event_and_returns_true(self):
        sid, ev = _make_stream_id()
        result = asyncio.get_event_loop().run_until_complete(
            self._call_cancel({"stream_id": sid})
        )
        data = result.body if hasattr(result, 'body') else b'{}'
        import json
        parsed = json.loads(data)
        assert parsed.get("cancelled") is True
        assert ev.is_set()
        assert sid not in _CANCEL_TOKENS  # token consommé

    def test_unknown_stream_id_returns_false(self):
        result = asyncio.get_event_loop().run_until_complete(
            self._call_cancel({"stream_id": "dead-stream-000"})
        )
        import json
        data = result.body if hasattr(result, 'body') else b'{}'
        parsed = json.loads(data)
        assert parsed.get("cancelled") is False

    def test_missing_stream_id_returns_false(self):
        result = asyncio.get_event_loop().run_until_complete(
            self._call_cancel({})
        )
        import json
        data = result.body if hasattr(result, 'body') else b'{}'
        parsed = json.loads(data)
        assert parsed.get("cancelled") is False

    def test_empty_stream_id_returns_false(self):
        result = asyncio.get_event_loop().run_until_complete(
            self._call_cancel({"stream_id": "   "})
        )
        import json
        data = result.body if hasattr(result, 'body') else b'{}'
        parsed = json.loads(data)
        assert parsed.get("cancelled") is False

    def test_double_cancel_second_returns_false(self):
        """Le second appel cancel sur le même stream_id échoue gracieusement."""
        sid, ev = _make_stream_id()
        loop = asyncio.get_event_loop()
        r1 = loop.run_until_complete(self._call_cancel({"stream_id": sid}))
        # Token déjà consommé, second cancel
        r2 = loop.run_until_complete(self._call_cancel({"stream_id": sid}))
        import json
        p1 = json.loads(r1.body if hasattr(r1, 'body') else b'{}')
        p2 = json.loads(r2.body if hasattr(r2, 'body') else b'{}')
        assert p1.get("cancelled") is True
        assert p2.get("cancelled") is False


# ─────────────────────────────────────────────────────────────────────────────
# Scénario race condition — Stop avant stream_id
# ─────────────────────────────────────────────────────────────────────────────

class TestEarlyStopRace:
    """Simule le scénario complet : Stop cliqué avant réception du stream_id."""

    def test_cancel_event_set_on_disconnect_before_stream_id(self):
        """
        Scénario:
        1. generate() démarre, crée stream_id et cancel_event
        2. Client reçoit l'événement 'start' mais PAS encore 'stream_id'
        3. Client déconnecte (AbortController.abort() → GeneratorExit)
        4. Le finally de generate() doit set(cancel_event)
        → Le thread agent verra cancel_event.is_set() et s'arrêtera
        """
        ev = threading.Event()
        # Simuler generate() avec un yield avant l'émission du stream_id
        def simulate_generate():
            try:
                yield "start"            # Client reçoit ça
                # ← Client clique Stop ici (entre start et stream_id)
                yield "stream_id"        # Jamais reçu
                yield "done"
            finally:
                ev.set()                 # ← Le fix

        gen = simulate_generate()
        next(gen)    # Client reçoit 'start'
        gen.close()  # Déconnexion client avant 'stream_id'

        assert ev.is_set(), (
            "Le thread agent doit recevoir le signal d'arrêt même si le client "
            "s'est déconnecté avant de recevoir le stream_id"
        )

    def test_agent_thread_sees_cancel_signal(self):
        """Le thread agent vérifie cancel_event entre itérations."""
        ev = threading.Event()
        ready = threading.Event()   # synchronisation : thread prêt à l'itération 3
        results = []

        def agent_loop():
            for i in range(10):
                if i == 3:
                    ready.set()           # signale que le thread est à l'itération 3
                    ev.wait(timeout=2.0)  # attend le signal d'annulation
                if ev.is_set():
                    results.append("cancelled_at_iteration_" + str(i))
                    return
                results.append(f"iteration_{i}")

        t = threading.Thread(target=agent_loop, daemon=True)
        t.start()
        ready.wait(timeout=2.0)  # attendre que le thread soit prêt
        ev.set()                 # déclencher l'annulation
        t.join(timeout=2.0)

        assert any("cancelled" in r for r in results), (
            "Le thread agent doit s'arrêter quand cancel_event est set"
        )

    def test_pending_cancel_fires_when_stream_id_arrives(self):
        """
        Simule le comportement côté client :
        _pendingCancel = True (set au moment du Stop)
        → quand stream_id arrive, cancel est envoyé immédiatement
        """
        # Simuler l'état client
        pending_cancel = True
        stream_id = None
        cancel_calls = []

        def on_stream_id(sid):
            nonlocal stream_id, pending_cancel
            stream_id = sid
            if pending_cancel:
                # Reproduit exactement le code JS fixé
                cancel_calls.append({"stream_id": sid})
                stream_id = None
                pending_cancel = False

        # Simuler l'arrivée du stream_id SSE
        fake_sid = "abc-123"
        on_stream_id(fake_sid)

        assert len(cancel_calls) == 1, "Le cancel doit être déclenché"
        assert cancel_calls[0]["stream_id"] == fake_sid
        assert not pending_cancel, "_pendingCancel doit être remis à False"
        assert stream_id is None, "_currentStreamId doit être vidé après cancel"

    def test_no_pending_cancel_stream_id_preserved(self):
        """Sans _pendingCancel, stream_id est simplement stocké."""
        pending_cancel = False
        stream_id = None
        cancel_calls = []

        def on_stream_id(sid):
            nonlocal stream_id, pending_cancel
            stream_id = sid
            if pending_cancel:
                cancel_calls.append({"stream_id": sid})
                stream_id = None
                pending_cancel = False

        on_stream_id("xyz-789")

        assert len(cancel_calls) == 0, "Pas de cancel sans _pendingCancel"
        assert stream_id == "xyz-789", "stream_id doit être conservé"


# ─────────────────────────────────────────────────────────────────────────────
# Vérification statique : pas de ctypes / arrêt forcé dans chat.py
# ─────────────────────────────────────────────────────────────────────────────

class TestNoForcedThreadKill:
    """Garantit que l'arrêt forcé par ctypes a été supprimé et ne peut pas réapparaître."""

    def _chat_source(self):
        import inspect
        import web.routes.chat as _chat
        return inspect.getsource(_chat)

    def test_no_ctypes_in_cancel_path(self):
        """ctypes ne doit plus apparaître dans le code de chat.py."""
        src = self._chat_source()
        # On cherche l'import ctypes spécifique à l'ancien kill forcé
        assert "PyThreadState_SetAsyncExc" not in src, (
            "PyThreadState_SetAsyncExc a été réintroduit — interdit car il peut "
            "interrompre un tool call en cours et laisser un état incohérent"
        )

    def test_no_systemexit_injection(self):
        """SystemExit ne doit pas être injecté dans un thread externe."""
        src = self._chat_source()
        # Vérifier l'absence de l'appel spécifique (pas l'exception elle-même)
        assert "py_object(SystemExit)" not in src, (
            "L'injection de SystemExit dans un thread via ctypes a été réintroduite"
        )

    def test_cooperative_cancel_still_present(self):
        """L'annulation coopérative (asyncio tasks cancel) doit rester."""
        src = self._chat_source()
        assert "call_soon_threadsafe" in src, (
            "L'annulation asyncio coopérative (call_soon_threadsafe) a disparu"
        )
        assert "cancel_event.set()" in src, (
            "cancel_event.set() dans le finally doit rester présent"
        )

    def test_react_cancel_events_registry_used(self):
        """Le registre _REACT_CANCEL_EVENTS doit rester câblé."""
        src = self._chat_source()
        assert "_REACT_CANCEL_EVENTS" in src, (
            "Le registre _REACT_CANCEL_EVENTS a disparu — "
            "la boucle ReAct ne recevrait plus le signal coopératif"
        )
