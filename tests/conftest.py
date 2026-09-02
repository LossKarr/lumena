"""
🧪 LUMENA - Fixtures de Test (Phase 5.4)

Fixtures partagées pour l'isolation et la reproductibilité des tests.
"""

import os
import sys
import pytest
import tempfile
import asyncio
import threading
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from typing import Generator, Any

# ── Fix DÉFINITIF: patch pytest-asyncio _provide_clean_event_loop ──────────
# pytest-asyncio 0.21 appelle _provide_clean_event_loop() à chaque teardown
# de test async. Elle appelle policy.new_event_loop() → ProactorEventLoop
# → _make_self_pipe() → socket.socketpair() TCP → TIME_WAIT.
# Avec 3403 tests × ~2 sockets = ~6806 entrées TIME_WAIT (120-240s Windows).
# Après le run, les 16384 ports éphémères sont épuisés → R2 crashe.
#
# PATCH: si le loop courant est encore ouvert (session loop), ne rien faire.
# → 0 nouveau socketpair pendant les tests. Seul le cleanup final en crée 1.
# Total: 2 socketpairs par run au lieu de ~6806. Aucune saturation possible.
try:
    import pytest_asyncio.plugin as _pa_plugin

    def _patched_provide_clean_event_loop() -> None:
        """Ne crée un nouveau loop QUE si l'actuel est fermé/absent.
        
        Original: crée TOUJOURS un nouveau loop → 1 socketpair TCP par test.
        Patché: réutilise le loop de session s'il est encore ouvert → 0 socketpair.
        """
        policy = asyncio.get_event_loop_policy()
        try:
            loop = policy.get_event_loop()
            if not loop.is_closed():
                return  # Loop de session encore ouvert → ne rien faire
        except RuntimeError:
            pass
        # Loop fermé ou absent (fin de session) → en créer un propre
        # Threaded pour éviter hang sur ProactorEventLoop.socketpair() Windows
        import threading as _th
        _result = [None]
        def _create():
            try:
                _result[0] = policy.new_event_loop()
            except Exception:
                pass
        t = _th.Thread(target=_create, daemon=True)
        t.start()
        t.join(timeout=3)
        if _result[0] is not None:
            policy.set_event_loop(_result[0])

    if hasattr(_pa_plugin, '_provide_clean_event_loop'):
        _pa_plugin._provide_clean_event_loop = _patched_provide_clean_event_loop

except Exception:
    pass  # Sécurité: si pytest-asyncio change d'API, on ne plante pas

# ── Patch Runner.close pour timeout sur ProactorEventLoop ──────────────────
# Sur Windows, Runner.close() → _cancel_all_tasks(loop) →
# loop.run_until_complete(gather(*to_cancel)) hang si des transports IOCP
# (subprocess, pipes) sont encore ouverts. Wrappons close() avec un timeout.
try:
    _OrigRunner = asyncio.Runner
    _orig_runner_close = _OrigRunner.close

    def _runner_close_with_timeout(self):
        """Runner.close() avec timeout de 3s pour éviter les hangs."""
        t = threading.Thread(target=_orig_runner_close, args=(self,), daemon=True)
        t.start()
        t.join(timeout=3)

    _OrigRunner.close = _runner_close_with_timeout
except Exception:
    pass


# ── Suppression des erreurs GC asyncio après fermeture du loop ─────────────
# Avec session-scoped event_loop, certains transports (subprocess pipes) sont
# GC'd après la fermeture du loop → ValueError: I/O operation on closed pipe.
# sys.unraisablehook capte ces erreurs de __del__ (sys.stderr.write ne suffit pas).
_orig_unraisablehook = getattr(sys, 'unraisablehook', None)

def _suppress_asyncio_gc_errors(unraisable):
    exc = unraisable.exc_value
    if exc is not None and isinstance(exc, (ValueError, ResourceWarning, RuntimeError)):
        msg = str(exc)
        if "closed pipe" in msg or "unclosed transport" in msg:
            return
        if "Event loop is closed" in msg:
            return
    if _orig_unraisablehook:
        _orig_unraisablehook(unraisable)

sys.unraisablehook = _suppress_asyncio_gc_errors


# ============================================================================
# Configuration globale
# ============================================================================

def pytest_configure(config):
    """Configuration pytest."""
    # Ajouter le path src pour les imports
    src_path = Path(__file__).parent.parent / "src"
    if str(src_path) not in sys.path:
        sys.path.insert(0, str(src_path))
    
    # Désactiver les logs verbeux pendant les tests
    os.environ.setdefault("LUMENA_LOG_LEVEL", "WARNING")


def pytest_sessionfinish(session, exitstatus):
    """Cleanup global avant la fermeture."""
    try:
        import src.hooks.hook_system as _hs_mod
        _hs_mod._hook_system = None
    except Exception:
        pass

    # Filtre stderr pour supprimer les warnings asyncio de fin de session
    try:
        import sys as _sys
        _original_stderr_write = _sys.stderr.write
        def _filtered_stderr_write(s):
            if "Task was destroyed but it is pending" in s:
                return 0
            if "ProactorEventLoop" in s and "_ssock" in s:
                return 0
            if "RuntimeError: Event loop is closed" in s:
                return 0
            if "I/O operation on closed pipe" in s:
                return 0
            if "_ProactorBasePipeTransport.__del__" in s:
                return 0
            if "BaseSubprocessTransport.__del__" in s:
                return 0
            if "unclosed transport" in s:
                return 0
            return _original_stderr_write(s)
        _sys.stderr.write = _filtered_stderr_write
    except Exception:
        pass


# ============================================================================
# Fixtures - Répertoires temporaires
# ============================================================================

@pytest.fixture
def temp_data_dir() -> Generator[Path, None, None]:
    """
    Crée un répertoire data temporaire pour les tests.
    Nettoyé automatiquement après le test.
    """
    with tempfile.TemporaryDirectory(prefix="lumena_test_") as tmpdir:
        data_dir = Path(tmpdir) / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        
        # Créer les sous-répertoires standards
        (data_dir / "memory").mkdir(exist_ok=True)
        (data_dir / "logs").mkdir(exist_ok=True)
        (data_dir / "cache").mkdir(exist_ok=True)
        
        yield data_dir


@pytest.fixture
def temp_workspace() -> Generator[Path, None, None]:
    """
    Crée un workspace temporaire pour tester les opérations fichier.
    """
    with tempfile.TemporaryDirectory(prefix="lumena_workspace_") as tmpdir:
        workspace = Path(tmpdir)
        
        # Créer quelques fichiers de test
        (workspace / "test.py").write_text("# Test file\nprint('hello')")
        (workspace / "subdir").mkdir()
        (workspace / "subdir" / "nested.txt").write_text("Nested content")
        
        yield workspace


# ============================================================================
# Fixtures - Mocks LLM
# ============================================================================

@pytest.fixture
def mock_llm():
    """
    Mock du client LLM pour éviter les appels réseau.
    
    Usage:
        def test_something(mock_llm):
            mock_llm.chat.return_value = "Response"
    """
    mock = AsyncMock()
    mock.chat = AsyncMock(return_value="Mocked LLM response")
    mock.is_available = AsyncMock(return_value=True)
    mock.model = "mock-model"
    mock.provider = MagicMock()
    mock.provider.value = "mock"
    mock.get_last_response_meta = MagicMock(return_value={
        "provider_used": "mock",
        "model_used": "mock-model",
        "continuation_used": False,
        "continuation_steps": 0,
        "finish_reason": "stop",
        "continuation_warning": None,
    })
    
    return mock


@pytest.fixture
def mock_llm_with_continuation():
    """
    Mock du client LLM qui simule une continuation.
    """
    mock = AsyncMock()
    
    # Premier appel retourne length, second retourne stop
    mock.chat = AsyncMock(side_effect=[
        "First part of response...",
        " continuation of response."
    ])
    mock.is_available = AsyncMock(return_value=True)
    mock.get_last_response_meta = MagicMock(return_value={
        "continuation_used": True,
        "continuation_steps": 1,
        "continuation_warning": None,
    })
    
    return mock


# ============================================================================
# Fixtures - Lumena Core
# ============================================================================

@pytest.fixture
def fresh_lumena(temp_data_dir, mock_llm):
    """
    Instance fraîche de LumenaCore avec isolation complète.
    
    Usage:
        def test_lumena(fresh_lumena):
            result = await fresh_lumena.process("hello")
    """
    # Réinitialiser le singleton
    import src.core as core_module
    
    # Sauvegarder et reset le singleton
    old_instance = getattr(core_module, '_lumena_instance', None)
    core_module._lumena_instance = None
    
    with patch.dict(os.environ, {"LUMENA_DATA_DIR": str(temp_data_dir)}):
        with patch('src.core.MultiProviderLLM', return_value=mock_llm):
            from src.core import LumenaCore
            
            try:
                lumena = LumenaCore(data_dir=temp_data_dir)
                yield lumena
            finally:
                # Restaurer le singleton
                core_module._lumena_instance = old_instance


@pytest.fixture
def mock_lumena_core():
    """
    Mock complet de LumenaCore pour tests rapides.
    """
    mock = MagicMock()
    mock.process = AsyncMock(return_value="Mocked response")
    mock.process_with_thinking = AsyncMock(return_value=("thinking", "response"))
    mock.llm = MagicMock()
    mock.memory = MagicMock()
    mock.data_dir = Path("/tmp/mock_data")
    
    return mock


# ============================================================================
# Fixtures - Memory
# ============================================================================

@pytest.fixture
def mock_chromadb(temp_data_dir):
    """
    Mock de ChromaDB pour les tests mémoire.
    """
    mock = MagicMock()
    mock.add = MagicMock()
    mock.search = MagicMock(return_value=[])
    mock.count = MagicMock(return_value=0)
    mock.delete = MagicMock()
    
    return mock


@pytest.fixture
def temp_memory(temp_data_dir):
    """
    Instance de mémoire temporaire avec vrai ChromaDB.
    """
    try:
        from src.memory.chromadb_store import ChromaDBMemory
        
        memory = ChromaDBMemory(data_dir=temp_data_dir)
        yield memory
        
        # Cleanup
        try:
            memory.clear()
        except Exception:
            pass
    except ImportError:
        pytest.skip("ChromaDB not installed")


# ============================================================================
# Déterminisme env — interrupteur maître réseau P2P
# ============================================================================

@pytest.fixture(autouse=True)
def _neutralize_peer_master(monkeypatch):
    """Neutralise `LUMENA_PEER_ENABLED` pour CHAQUE test.

    Le maître réseau (OR-fallback) rallume toutes les capacités P2P. S'il fuit du
    `.env` du dev (qui peut l'avoir activé via l'UI), les tests « flag OFF → refuse »
    échouent à tort. On le retire par défaut ; les tests qui veulent le maître ON
    l'activent explicitement via monkeypatch.setenv.
    """
    monkeypatch.delenv("LUMENA_PEER_ENABLED", raising=False)
    monkeypatch.delenv("LUMENA_PEER_HALT", raising=False)
    monkeypatch.delenv("LUMENA_PEER_AUTONOMY", raising=False)


@pytest.fixture(autouse=True)
def _neutralize_mission_journal(monkeypatch):
    """La suite n'ECRIT JAMAIS dans les donnees reelles.

    Le journal de mission est accroche a `TraceBus.publish` : tout evenement
    portant un `task_id` grave une ligne dans `data/missions/<id>.jsonl`. Douze
    fichiers de tests publient des traces — sans ce garde, une simple
    regression sement des dizaines de journaux fantomes dans les donnees du
    developpeur, et les fait grossir a chaque execution.

    Meme raison que les deux garde-fous ci-dessus : un test ne doit pas laisser
    de trace hors de son `tmp_path`. Les fichiers qui testent le journal
    lui-meme le rallument explicitement ET redirigent sa racine.
    """
    monkeypatch.setenv("LUMENA_MISSION_JOURNAL", "0")


@pytest.fixture(autouse=True)
def _neutralize_codex_subscription(monkeypatch):
    """LOT Z33 — la suite est ÉTANCHE à l'abonnement Codex.

    Même cause, même remède que `_neutralize_peer_master` juste au-dessus :
    `LUMENA_OPENAI_ACCESS_MODE=chatgpt_codex` fuit du `.env` du dev dès qu'il
    active l'abonnement dans l'UI. Mesuré le 21/08 :

      • la suite passe de **9 à 47 minutes** ;
      • 5 tests échouent — dont `test_delegate_task_success` — parce qu'ils font
        de VRAIS tours Codex (au log : « [Agent/Codex] tour termine
        model=gpt-5.6-sol ») au lieu de leurs doublures ;
      • du quota d'abonnement est consommé pour rien.

    Preuve : ces 5 tests passent (74/74) dès que le mode est forcé à `api`.

    Un test ne doit jamais dépendre d'un abonnement réel : ni pour son résultat,
    ni pour sa durée. Les tests qui veulent l'abonnement l'activent
    explicitement via `monkeypatch.setenv` — ils continuent de fonctionner.
    """
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    # API mode may retain a configured Codex model for rescue. Tests opt in
    # explicitly so a developer's real subscription never answers by accident.
    monkeypatch.setenv("LUMENA_CODEX_API_RESCUE", "0")


# ============================================================================
# Fixtures - Async
# ============================================================================

@pytest.fixture(scope="session", autouse=True)
def event_loop():
    """Session-scoped autouse: créé avant tout test (sync ou async).
    
    autouse=True garantit que asyncio.set_event_loop() est appelé dès le
    début de session, avant que _run() dans les tests sync ne soit invoqué.
    scope='session' = 1 seul ProactorEventLoop/run = 1 socketpair TCP/run
    → impossible d'épuiser les 16384 ports éphémères Windows (TIME_WAIT).
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield loop

    # Cancel toutes les tasks pendantes
    try:
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for t in pending:
            t.cancel()
            try:
                t._log_destroy_pending = False
            except Exception:
                pass
    except RuntimeError:
        pass

    # Fermer dans un daemon thread (ProactorEventLoop.close peut hang sur Windows)
    def _close():
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass

    closer = threading.Thread(target=_close, daemon=True)
    closer.start()
    closer.join(timeout=3)
    # Si ça hang encore, le daemon thread meurt avec le process


@pytest.fixture(autouse=True)
def _ensure_current_event_loop(event_loop):
    """Rétablit un event loop courant ouvert pour les tests sync.

    Certains tests/fixtures async ferment le loop courant laissé dans la policy.
    Les tests synchrones qui font encore `asyncio.get_event_loop().run_until_complete(...)`
    doivent toujours retrouver un loop valide.
    """
    try:
        current = asyncio.get_event_loop_policy().get_event_loop()
        if current.is_closed():
            asyncio.set_event_loop(event_loop)
    except RuntimeError:
        asyncio.set_event_loop(event_loop)

    yield

    try:
        current = asyncio.get_event_loop_policy().get_event_loop()
        if current.is_closed():
            asyncio.set_event_loop(event_loop)
    except RuntimeError:
        asyncio.set_event_loop(event_loop)


@pytest.fixture(autouse=True)
def _drain_hooks():
    """
    Nettoie le singleton HookSystem après chaque test.
    Fixture SYNCHRONE pour couvrir tous les tests (sync et async).
    """
    yield
    try:
        import src.hooks.hook_system as _hs_mod
        _hs_mod._hook_system = None
    except Exception:
        pass


# ============================================================================
# Fixtures - Environment
# ============================================================================

@pytest.fixture
def clean_env():
    """
    Environnement propre sans variables Lumena.
    """
    lumena_vars = [k for k in os.environ if k.startswith("LUMENA_")]
    saved = {k: os.environ.pop(k) for k in lumena_vars}
    
    yield
    
    # Restaurer
    os.environ.update(saved)


@pytest.fixture
def mock_env():
    """
    Variables d'environnement mockées pour les tests.
    """
    test_env = {
        "LUMENA_MODEL": "qwen3-8b",
        "LUMENA_DATA_DIR": "/tmp/lumena_test",
        "LUMENA_LOG_LEVEL": "WARNING",
        "LUMENA_MAX_REACT_ITERATIONS": "10",
        "LUMENA_REACT_TIMEOUT": "60",
    }
    
    with patch.dict(os.environ, test_env, clear=False):
        yield test_env


# ============================================================================
# Helpers
# ============================================================================

@pytest.fixture
def assert_no_logs(caplog):
    """
    Vérifie qu'aucun log d'erreur n'a été émis.
    """
    def checker():
        errors = [r for r in caplog.records if r.levelno >= 40]  # ERROR = 40
        assert not errors, f"Unexpected errors: {[r.message for r in errors]}"
    
    return checker


# ============================================================================
# Markers personnalisés
# ============================================================================

def pytest_collection_modifyitems(config, items):
    """Ajoute des markers automatiques."""
    for item in items:
        # Marquer les tests async
        if asyncio.iscoroutinefunction(item.obj):
            item.add_marker(pytest.mark.asyncio)
        
        # Marquer les tests lents
        if "slow" in item.nodeid:
            item.add_marker(pytest.mark.slow)
