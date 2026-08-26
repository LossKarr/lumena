"""LOT Z33 — la session Codex survit au redémarrage.

Run du 2026-08-21, mesuré au log :

    02:28:55  mission créée (duree_minutes=90 → échéance 03:58 : Z32 marche)
    02:28→02:32  Codex travaille — CodeAgent, agent, 46 outils sur un tour
    02:33:42  [resume] 1 mission relancée au démarrage      ← REDÉMARRAGE
    02:33:54  [mission-worker] démarré
    02:33:57  ÉCHEC : « Aucune session Codex connectee. […]
                       Aucun fallback API n'a ete utilise. »

`_shared_codex_app_server` est un global de MODULE, posé uniquement par la route
web quand l'utilisateur clique dans Configuration > Accès OpenAI. Il meurt avec
le process. Les missions, elles, sont **persistées et relancées** au boot.

Au redémarrage : la mission repart, la session non. Elle cherche le superviseur,
ne le trouve pas, et meurt — sans repli, c'était écrit en dur dans le message.

C'est l'écart exact avec un modèle API : une clé dans `.env` survit à tout
redémarrage, une session en mémoire non.

`lifespan` ARRÊTAIT la session (`stop_attached_codex_app_server`) sans jamais la
rouvrir : le pendant au démarrage n'existait pas.

⚠️ VÉRIFIÉ EN DIRECT avant d'écrire une ligne, sur l'abonnement réel :

    app-server démarré : True
    compte  : CONNECTED   plan : plus
    quota   : 28 % sur fenêtre 7 jours
    modèles : 6 (gpt-5.6-sol par défaut, terra, luna, 5.5, 5.4, 5.4-mini)

Le compte était déjà `CONNECTED` depuis un script NEUF : **l'auth est stockée sur
disque par le CLI lui-même**. Il n'y a donc rien à reconnecter — juste un
processus à relancer. C'est ce qui rend ce correctif petit et sûr.

Ces tests ne lancent AUCUN processus : `access_mode=api` par défaut rend la
fonction inerte, et les sessions sont simulées.
"""

import asyncio
from pathlib import Path

import pytest

from src.llm import codex_app_server as cas


# ── Bancs ────────────────────────────────────────────────────────────────────


class _FausseSession:
    """Un superviseur qui tourne, sans processus."""

    def __init__(self, running: bool = True):
        self.is_running = running
        self.arrets = 0

    async def stop(self):
        self.arrets += 1
        self.is_running = False


@pytest.fixture(autouse=True)
def _session_propre(monkeypatch):
    """Aucun test ne doit fuir sa session sur le suivant."""
    monkeypatch.setattr(cas, "_shared_codex_app_server", None, raising=False)
    yield
    monkeypatch.setattr(cas, "_shared_codex_app_server", None, raising=False)


def _ensure(**kw):
    return asyncio.run(cas.ensure_shared_codex_app_server(**kw))


# ── Le comportement ──────────────────────────────────────────────────────────


def test_une_session_vivante_est_reutilisee(monkeypatch):
    """Ouvrir une SECONDE session laisserait un processus orphelin et deux
    files de notifications concurrentes."""
    vivante = _FausseSession()
    monkeypatch.setattr(cas, "_shared_codex_app_server", vivante, raising=False)
    assert _ensure() is vivante


def test_une_session_morte_n_est_pas_rendue(monkeypatch):
    """`get_shared_codex_app_server` ne rend jamais d'instance périmée : c'est
    ce contrat qui permet la réouverture."""
    morte = _FausseSession(running=False)
    monkeypatch.setattr(cas, "_shared_codex_app_server", morte, raising=False)
    assert cas.get_shared_codex_app_server() is None


def test_abonnement_desactive_ne_lance_rien(monkeypatch):
    """API sans secours configure → comportement historique, aucun process."""
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.delenv("LUMENA_CODEX_DEFAULT_MODEL", raising=False)
    assert _ensure() is None


def test_secours_codex_api_survit_au_redemarrage(monkeypatch):
    """API primaire + modele Codex conserve doit pouvoir rouvrir App Server.

    Sinon le secours source-aware fonctionne dans les tests mocks mais meurt au
    premier reboot, car aucun superviseur partage n'existe encore.
    """
    import src.llm.codex_subscription as csub

    settings = csub.CodexSubscriptionSettings(
        access_mode=csub.OpenAIAccessMode.API,
        default_model="gpt-5.6-sol",
        api_rescue_enabled=True,
    )

    class _Preflight:
        state = csub.CodexCLIState.READY
        executable = "codex.exe"
        detail = "ready"

    class _Supervisor(_FausseSession):
        def __init__(self, _config):
            super().__init__(running=False)

        async def start(self):
            self.is_running = True

    async def _probe(_path=None):
        return _Preflight()

    monkeypatch.setattr(csub, "load_codex_subscription_settings", lambda: settings)
    monkeypatch.setattr(csub, "probe_codex_cli_async", _probe)
    monkeypatch.setattr(cas, "CodexAppServerSupervisor", _Supervisor)

    supervisor = _ensure()

    assert isinstance(supervisor, _Supervisor)
    assert supervisor.is_running is True
    assert cas.get_shared_codex_app_server() is supervisor


def test_ne_leve_jamais_meme_si_tout_casse(monkeypatch):
    """Au boot, une exception ici empêcherait Lumena de démarrer."""
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")

    def _boom(*a, **k):
        raise RuntimeError("preflight casse")

    import src.llm.codex_subscription as csub
    monkeypatch.setattr(csub, "probe_codex_cli_async", _boom, raising=False)
    monkeypatch.setattr(csub, "load_codex_subscription_settings", _boom, raising=False)
    assert _ensure() is None


def test_un_app_absent_ne_pose_pas_de_probleme(monkeypatch):
    """Appelée depuis le runtime (pas depuis le web), il n'y a pas d'`app`.

    ⚠️ Le mode est forcé EXPLICITEMENT. Sans ça, ce test dépendait de
    l'environnement ambiant : dans la suite complète il a trouvé
    `chatgpt_codex`, lancé un VRAI app-server et échoué. Un test qui démarre un
    processus selon l'ordre d'exécution n'est pas un test.
    """
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.delenv("LUMENA_CODEX_DEFAULT_MODEL", raising=False)
    assert _ensure(app=None) is None


# ── Le branchement au démarrage ──────────────────────────────────────────────


_LIFESPAN = Path("web/routes/lifespan.py").read_text(encoding="utf-8")


def test_la_session_est_rouverte_au_boot():
    assert "ensure_shared_codex_app_server(app)" in _LIFESPAN


def test_la_reouverture_precede_le_worker_de_missions():
    """LE test du lot — et l'invariant a été corrigé en cours de route.

    J'avais d'abord écrit « la session s'ouvre avant `relaunch_queued` ». C'était
    imprécis : `relaunch_queued` ne fait qu'EMPILER dans la file (`put_nowait`).
    Au log du 21/08, relance à 02:33:42, worker démarré à 02:33:54, exécution à
    02:33:55 — douze secondes plus tard.

    Le vrai invariant est donc : la session doit être ouverte avant que le
    WORKER ne consomme la file. Ça laisse la place à la garde `if _z33_relaucees`
    — n'ouvrir un processus que s'il y a réellement quelque chose à reprendre.
    """
    i_session = _LIFESPAN.index("ensure_shared_codex_app_server(app)")
    i_worker = _LIFESPAN.index("start_mission_worker(deps.lumena)")
    assert i_session < i_worker


def test_aucun_processus_n_est_lance_sans_mission_a_reprendre():
    """Sans cette garde, CHAQUE démarrage d'app en mode abonnement lançait un
    app-server — y compris les dizaines de boots de la suite de tests, qui est
    passée de 9 à 47 minutes."""
    i = _LIFESPAN.index("ensure_shared_codex_app_server(app)")
    bloc = _LIFESPAN[i - 900:i]
    assert "if _z33_relaucees:" in bloc
    assert "_z33_relaucees = relaunch_queued(" in bloc


def test_l_arret_avait_bien_un_pendant_manquant():
    """Garde-fou historique : `stop_` existait seul. Si le `ensure_` disparaît,
    on retombe dans le trou."""
    assert "stop_attached_codex_app_server" in _LIFESPAN
    assert "ensure_shared_codex_app_server" in _LIFESPAN


def test_le_boot_ne_peut_pas_echouer_sur_codex():
    i = _LIFESPAN.index("ensure_shared_codex_app_server(app)")
    bloc = _LIFESPAN[i - 400:i + 300]
    assert "try:" in bloc and "except Exception" in bloc


# ── La reconnexion à chaud, et la fin du « aucun fallback » ──────────────────


_ROUTER = Path("src/llm/execution_router.py").read_text(encoding="utf-8")


def test_une_session_manquante_declenche_une_reouverture():
    """Avant : échec sec. Maintenant : on tente de rouvrir d'abord."""
    i = _ROUTER.index("Aucune session Codex connectee")
    bloc = _ROUTER[i - 900:i]
    assert "ensure_shared_codex_app_server" in bloc


def test_le_message_ne_promet_plus_l_absence_de_repli():
    """« Aucun fallback API n'a ete utilise » était écrit en dur : c'était la
    formulation d'un abandon, pas d'une tentative."""
    assert "Aucun fallback API n'a ete utilise" not in _ROUTER


def test_le_message_dit_ce_qui_a_ete_tente():
    i = _ROUTER.index("Aucune session Codex connectee")
    assert "reouverture automatique tentee" in _ROUTER[i:i + 300]


def test_la_reouverture_a_chaud_est_tracee():
    """Sans trace, on ne saura jamais si le correctif a servi."""
    assert "[Z33] session Codex rouverte a chaud" in _ROUTER


def test_la_reouverture_ne_peut_pas_casser_le_run():
    i = _ROUTER.index("ensure_shared_codex_app_server")
    bloc = _ROUTER[i - 300:i + 400]
    assert "except Exception" in bloc


# ── Le code porte sa raison ──────────────────────────────────────────────────


_CAS = Path("src/llm/codex_app_server.py").read_text(encoding="utf-8")


def test_la_raison_du_lot_est_datee_dans_le_code():
    entete = _CAS[_CAS.index("LOT Z33 phase 0"):][:2200]
    assert "02:33:57" in entete
    assert "CONNECTED" in entete


def test_la_decouverte_qui_simplifie_est_consignee():
    """Que l'auth survive sur disque est LA raison pour laquelle ce correctif
    ne gère aucune reconnexion. Si ce fait se perd, le prochain lecteur
    ajoutera un flux de login inutile."""
    entete = _CAS[_CAS.index("LOT Z33 phase 0"):][:2200]
    assert "auth est stockee sur disque" in entete
    assert "juste un processus a relancer" in entete


def test_l_import_tardif_est_justifie():
    """`codex_subscription` importe déjà ce module : un import en tête créerait
    un cycle. Sans le commentaire, quelqu'un « nettoiera » l'import un jour."""
    i = _CAS.index("from src.llm.codex_subscription import")
    assert "cycle" in _CAS[i - 300:i]
