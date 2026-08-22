"""LOT Z35 — un CodeAgent qui n'a rien écrit n'a pas réussi.

Run « gobelet motion » (2026-08-21). Au log :

    14:27:31  [create_project] Mode CodeAgent route=codex_subscription
    14:27:33  [CodeAgent/Codex] demarrage workspace=workspace\\gobelet-motion
    14:27:43  commande terminee: status=failed  exit=1
    14:27:48  commande terminee: status=completed exit=0

Sur le disque, vérifié : `workspace/gobelet-motion` contient **0 fichier**.
Et pourtant `run_codeagent_with_codex_subscription` renvoyait `success=True`.

`changed = _changed_files(before, execution_root)` était calculé TROIS LIGNES
plus haut et jamais consulté : le diff vide et le diff plein donnaient le même
verdict. Le chemin `validation_failed` juste après ne rattrapait rien — la
boucle de syntaxe ne parcourt que `changed` (vide), et `tests_expected` est faux
dans un dossier vide.

Conséquence en cascade, tout le reste du run en découle : l'agent croyait son
site fait, l'a découvert absent, s'est rabattu sur `create_html`, a dû écrire
DEUX scripts PowerShell jetables (`fix-render.ps1`, `fix-encoding.ps1`) pour
réparer l'échappement HTML puis les accents. **~4 minutes sur 8 min 22.**

POURQUOI LA ROUTE API N'A PAS CE TROU — c'est la réponse à « pourquoi ça ne
marche pas pareil qu'avec les modèles API » :

  • CodeAgent classique  → écrit OUTIL PAR OUTIL (`write_file` → registre →
    ledger). « Rien écrit » y est une ABSENCE, visible toute seule.
  • CodeAgent Codex      → boîte noire puis DIFF, recopié à la fin. « Rien
    écrit » y devient un RÉSULTAT qu'il faut regarder.

Le principe boîte-noire-puis-diff reste bon — c'est la bonne façon d'intégrer un
agent CLI autonome. Il lui manquait ce regard.
"""

import asyncio
from collections import deque
from pathlib import Path

import pytest

from src.llm.codex_app_server import CodexNotification
from src.llm.codex_codeagent import run_codeagent_with_codex_subscription
from src.llm.codex_subscription import (
    CodexSubscriptionSettings,
    CodexSurface,
    OpenAIAccessMode,
)


# ── Banc (même forme que test_codeagent_codex_subscription, autonome) ────────


def _settings(model: str = "codex-model"):
    return CodexSubscriptionSettings(
        access_mode=OpenAIAccessMode.CHATGPT_CODEX,
        default_model=model,
        surfaces=frozenset({CodexSurface.CODEAGENT}),
    )


class _Superviseur:
    def __init__(self, mutate=None, notifications=()):
        self.is_running = True
        self.mutate = mutate
        self.notifications = deque(notifications)
        self.requests = []

    async def request(self, method, params=None, *, timeout=None):
        self.requests.append((method, params, timeout))
        if method == "account/read":
            return {"account": {"type": "chatgpt", "plan": "test"}}
        if method == "model/list":
            return {"models": [{"id": "codex-model", "isDefault": True}]}
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            if self.mutate:
                self.mutate(Path(params["cwd"]))
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(f"methode inattendue: {method}")

    async def next_notification(self, *, timeout=None):
        if not self.notifications:
            await asyncio.Event().wait()
        return self.notifications.popleft()


def _notif(method, params):
    return CodexNotification(method=method, params=params)


def _commande(status="completed", exit_code=0):
    return _notif(
        "item/completed",
        {
            "threadId": "thread-1",
            "turnId": "turn-1",
            "item": {
                "type": "commandExecution",
                "command": ["python", "-c", "pass"],
                "status": status,
                "exitCode": exit_code,
            },
        },
    )


def _fin(texte="Codex a termine la tache."):
    return [
        _notif(
            "item/completed",
            {
                "threadId": "thread-1",
                "turnId": "turn-1",
                "item": {"type": "agentMessage", "text": texte},
            },
        ),
        _notif(
            "turn/completed",
            {"threadId": "thread-1", "turn": {"id": "turn-1", "status": "completed"}},
        ),
    ]


def _lancer(tmp_path, mutate, notifications, **kw):
    workspace = tmp_path / "projet"
    workspace.mkdir()
    sup = _Superviseur(mutate, notifications)
    resultat = asyncio.run(
        run_codeagent_with_codex_subscription(
            "Cree le site.",
            agent_type="code",
            context={"user_original_request": "genere moi un site"},
            workspace_path=workspace,
            # scope=0, comme le run reel : sans perimetre, `execution_root`
            # EST le workspace — Codex y ecrit directement, aucune recopie.
            allowed_files=None,
            settings=_settings(),
            supervisor=sup,
            **kw,
        )
    )
    return resultat, workspace


# ══════════════════════════════════════════════════════════════════════════════
#  Le cas mesuré
# ══════════════════════════════════════════════════════════════════════════════


def test_zero_fichier_ecrit_n_est_plus_un_succes(tmp_path):
    """LE lot. Codex tourne, ne pose aucun fichier — et annonçait `success=True`."""
    resultat, _ = _lancer(tmp_path, lambda root: None, _fin())
    assert resultat.success is False
    assert resultat.status_code == "no_change"


def test_le_run_du_21_08_est_reproduit(tmp_path):
    """Deux commandes dont une en échec, aucun fichier : exactement le run."""
    notifs = [_commande("failed", 1), _commande("completed", 0), *_fin()]
    resultat, workspace = _lancer(tmp_path, lambda root: None, notifs)
    assert resultat.success is False
    assert list(workspace.iterdir()) == []


def test_la_commande_en_echec_devient_visible(tmp_path):
    """`status=failed exit=1` n'existait que dans le log : l'appelant ne le
    voyait jamais. Sans ce détail, impossible de savoir POURQUOI rien n'a été
    écrit."""
    notifs = [_commande("failed", 1), _commande("completed", 0), *_fin()]
    resultat, _ = _lancer(tmp_path, lambda root: None, notifs)
    assert "exit=1" in resultat.output
    assert resultat.meta.get("command_failures") == ["exit=1"]


def test_le_message_dit_quoi_faire_ensuite(tmp_path):
    """Un échec sans issue laisse l'agent tourner en rond — c'est ce qui l'a mené
    à `create_html` puis aux deux scripts PowerShell."""
    resultat, _ = _lancer(tmp_path, lambda root: None, _fin())
    assert "AUCUN" in resultat.output
    assert "outils Lumena" in resultat.output


def test_aucun_fichier_n_est_annonce_comme_artefact(tmp_path):
    """Annoncer un artefact inexistant, c'est le mensonge que tout le chantier
    combat."""
    resultat, _ = _lancer(tmp_path, lambda root: None, _fin())
    assert not resultat.artifacts


# ══════════════════════════════════════════════════════════════════════════════
#  Non-régression : le cas nominal doit rester intact
# ══════════════════════════════════════════════════════════════════════════════


def test_un_vrai_travail_reste_un_succes(tmp_path):
    """LE garde anti-régression. Si ce test tombe, le lot a cassé le chemin
    normal — un CodeAgent qui écrit doit continuer de réussir."""
    def mutate(root: Path):
        (root / "index.html").write_text("<h1>ok</h1>\n", encoding="utf-8")

    resultat, workspace = _lancer(tmp_path, mutate, _fin())
    assert resultat.success is True
    assert resultat.status_code != "no_change"
    assert (workspace / "index.html").read_text(encoding="utf-8") == "<h1>ok</h1>\n"


def test_une_commande_en_echec_n_annule_pas_un_travail_reel(tmp_path):
    """Un `exit=1` en cours de route est normal (un test qui échoue puis qu'on
    répare). Seul le diff VIDE déclenche le refus."""
    def mutate(root: Path):
        (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    notifs = [_commande("failed", 1), _commande("completed", 0), *_fin()]
    resultat, _ = _lancer(tmp_path, mutate, notifs)
    assert resultat.success is True


def test_le_controle_precede_la_validation_syntaxique():
    """La validation syntaxique ne parcourt que `changed` : sur un diff vide
    elle ne fait rien et laisse passer. Le contrôle doit donc venir AVANT."""
    src = Path("src/llm/codex_codeagent.py").read_text(encoding="utf-8")
    i_z35 = src.index("if not changed:")
    i_syntaxe = src.index("syntax_errors: list[str] = []")
    assert i_z35 < i_syntaxe


def test_le_controle_suit_la_verification_de_perimetre():
    """Une écriture hors périmètre doit rester un `scope_violation`, pas être
    requalifiée en « rien écrit »."""
    src = Path("src/llm/codex_codeagent.py").read_text(encoding="utf-8")
    assert src.index("status_code=\"scope_violation\"") < src.index("if not changed:")


def test_la_raison_du_lot_est_datee_dans_le_code():
    src = Path("src/llm/codex_codeagent.py").read_text(encoding="utf-8")
    entete = src[src.index("LOT Z35"):][:2200]
    assert "14:27:43" in entete
    assert "outil par outil" in entete
