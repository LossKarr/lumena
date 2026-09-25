"""LOT 8a — « JE N'AI PAS PU VÉRIFIER » N'EST PAS « C'EST BON ».

═══════════════════════════════════════════════════════════════════════════════
  LE DÉFAUT
═══════════════════════════════════════════════════════════════════════════════

`check_syntax()` rendait `""` dans TROIS situations différentes :

    fichier validé, aucun défaut          →  ""
    extension non couverte (.rs .php .go) →  ""     ← indistinguable
    outil absent (node, ruff)             →  ""     ← indistinguable

Deux états pour trois situations. L'appelant ne pouvait donc pas distinguer un
fichier PROUVÉ correct d'un fichier JAMAIS REGARDÉ — et l'absence de warning se
lit « c'est bon ».

`_check_javascript` l'assumait jusque dans sa docstring : « best-effort,
silencieux si node absent ». Un `.js` sur une machine sans node était déclaré bon.

═══════════════════════════════════════════════════════════════════════════════
  CE QUI A ÉTÉ MESURÉ
═══════════════════════════════════════════════════════════════════════════════

Sur `workspace/` (hors node_modules, .git, venv) :

    code source VALIDÉ       2 450
    code source NON validé      85   dont 61 .php · 11 .sql · 8 .cs · 4 .lua

C'est le lot **Z38**, jamais refermé — « le CodeAgent corrigeait du PHP sans
interpréteur PHP : ~20 fichiers écrits, 0 validé, redéclarations réparées de tête
6 fois ».

Et la discipline injectée dans CHAQUE worker de code ordonne :

    • Après CHAQUE mutation significative, EXÉCUTE (module/tests concernés)
      avant de conclure — ne devine pas que « ça marche ».

En Rust, Go, Java ou PHP, cet ordre est IMPOSSIBLE À EXÉCUTER.

═══════════════════════════════════════════════════════════════════════════════
  LE CORRECTIF — UN TROISIÈME ÉTAT (comme Z40c)
═══════════════════════════════════════════════════════════════════════════════

Ce lot n'installe AUCUN compilateur : c'est le lot 8b/8c, et il demande une
décision (Docker ou toolchains locales). Il fait la seule chose qui vaille avant :
que l'agent SACHE qu'il est aveugle, et le DISE.

Doctrine Z40c — la porte laisse TOUJOURS passer, elle ne se tait pas. Bloquer un
projet Rust parce que la machine n'a pas `rustc` serait absurde ; le déclarer
vérifié est pire.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.utils import syntax_check as sc


def _ecrit(tmp_path: Path, nom: str, contenu: str) -> Path:
    p = tmp_path / nom
    p.write_text(contenu, encoding="utf-8")
    return p


# ══════════════════════════════════════════════════════════════════════════
#  1. LES TROIS ÉTATS EXISTENT ET SE DISTINGUENT
# ══════════════════════════════════════════════════════════════════════════


def test_un_fichier_valide_rend_OK(tmp_path):
    v, d = asyncio.run(sc.verify_syntax(_ecrit(tmp_path, "bon.py", "x = 1\n")))
    assert v == sc.OK and d == ""


def test_un_fichier_casse_rend_ERREUR(tmp_path):
    v, d = asyncio.run(sc.verify_syntax(_ecrit(tmp_path, "ko.py", "def f(:\n")))
    assert v == sc.ERREUR and d


@pytest.mark.parametrize("nom,contenu", [
    ("main.rs", "fn main() {}"),
    ("App.java", "class App {}"),
    ("q.sql", "SELECT 1;"),
    ("prog.cs", "class P {}"),
])
def test_un_langage_sans_validateur_rend_NON_VERIFIABLE(tmp_path, nom, contenu):
    """⚠️ RÉÉCRIT PAR LE LOT 8bc (2026-09-03) — et c'est la preuve qu'il marche.

    Ce test listait aussi `.php`, `.go` et `.sh`. Le lot 8bc cherche désormais
    l'outil hors du PATH puis dans Docker : sur une machine où `C:\\php\\php.exe`
    existe, un `.php` est RÉELLEMENT validé — il ne peut donc plus être ici.
    Le figer voudrait dire figer l'aveuglement.

    Ne restent que les langages qui exigent un PROJET, pas un fichier isolé :
    `rustc`, `javac`, `dotnet build`, `sqlfluff`. Ceux-là n'entreront jamais dans
    la table tant qu'on ne saura pas les vérifier honnêtement — les y mettre
    ferait croire à une validation qui n'en serait pas une.

    Les langages désormais couverts sont testés dans
    `test_lot8bc_chercher_avant_de_conclure.py`."""
    v, d = asyncio.run(sc.verify_syntax(_ecrit(tmp_path, nom, contenu)))
    assert v == sc.NON_VERIFIABLE
    assert Path(nom).suffix in d          # la raison NOMME l'extension
    assert "aucun validateur" in d


@pytest.mark.parametrize("ext", [".php", ".go", ".sh", ".rb", ".lua", ".pl"])
def test_les_langages_du_lot_8bc_ne_sont_PLUS_aveugles(ext):
    """LOT 8bc — ces extensions ont maintenant une voie de vérification (outil local
    ou image Docker). Leur verdict dépend de la machine, pas d'une lacune du code :
    c'est exactement ce que le lot a changé."""
    assert ext in sc._VALIDATEURS
    outil, args, _image = sc._VALIDATEURS[ext]
    assert outil and args


def test_les_trois_etats_sont_DISTINCTS():
    assert len({sc.OK, sc.ERREUR, sc.NON_VERIFIABLE}) == 3


# ══════════════════════════════════════════════════════════════════════════
#  2. L'OUTIL ABSENT N'EST PLUS UN SUCCÈS
# ══════════════════════════════════════════════════════════════════════════


def test_node_absent_ne_veut_plus_dire_correct(tmp_path, monkeypatch):
    """La docstring d'origine disait « silencieux si node absent ». Sur une machine
    sans node, tout le JavaScript écrit était donc réputé bon."""
    monkeypatch.setattr(sc.shutil, "which", lambda _: None)
    v, d = asyncio.run(sc.verify_syntax(_ecrit(tmp_path, "a.js", "let x = ;")))
    assert v == sc.NON_VERIFIABLE
    assert "node" in d and "NON vérifié" in d


def test_un_fichier_absent_est_NON_VERIFIABLE_pas_valide(tmp_path):
    v, d = asyncio.run(sc.verify_syntax(tmp_path / "fantome.py"))
    assert v == sc.NON_VERIFIABLE
    assert "absent" in d


# ══════════════════════════════════════════════════════════════════════════
#  3. RÉTROCOMPATIBILITÉ — `check_syntax` garde son contrat exact
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom,contenu,attendu_vide", [
    ("bon.py", "x = 1\n", True),
    ("bon.json", '{"a": 1}', True),
    ("bon.css", "a{color:red}", True),
    ("main.rs", "fn main(){}", True),      # non vérifiable → "" comme avant
    ("ko.json", "{bad", False),
    ("ko.css", "a{color:red", False),
])
def test_check_syntax_rend_toujours_une_chaine(tmp_path, nom, contenu, attendu_vide):
    """Ses deux appelants historiques attendent une `str` : "" = rien à signaler.
    Le lot n'a PAS changé ce contrat — il a ajouté `verify_syntax` à côté."""
    out = asyncio.run(sc.check_syntax(_ecrit(tmp_path, nom, contenu)))
    assert isinstance(out, str)
    assert (out == "") is attendu_vide


def test_check_syntax_ne_remonte_QUE_les_erreurs(tmp_path):
    """Un non-vérifiable ne doit pas passer pour une erreur : ce serait alarmer à
    tort sur du code peut-être parfait."""
    assert asyncio.run(sc.check_syntax(_ecrit(tmp_path, "a.rs", "fn main(){}"))) == ""


def test_le_flag_desactive_ne_declenche_AUCUNE_alarme(tmp_path, monkeypatch):
    """Couper les gates est un CHOIX de l'exploitant, pas une incapacité de la
    machine : ne pas transformer un réglage en avertissement permanent."""
    import src.config.codeagent_flags as flags
    monkeypatch.setattr(flags, "REACT_QUALITY_GATES", False, raising=False)
    v, _ = asyncio.run(sc.verify_syntax(_ecrit(tmp_path, "a.rs", "fn main(){}")))
    assert v == sc.OK


# ══════════════════════════════════════════════════════════════════════════
#  4. LE FAIT ATTEINT CELUI QUI DÉCIDE
# ══════════════════════════════════════════════════════════════════════════


def test_l_agent_est_PREVENU_apres_une_ecriture_non_verifiable(tmp_path):
    """LE CŒUR DU LOT. Une fonction que personne n'appelle ne vaut rien : sans
    cette remontée, l'absence de warning se lit « c'est bon » et l'agent conclut
    sur un fichier que personne n'a regardé (Z38)."""
    from src.reasoning.handlers.files import _append_syntax_warning
    cible = _ecrit(tmp_path, "lib.rs", "fn main(){}")
    out = asyncio.run(_append_syntax_warning("Fichier écrit.", cible))
    assert "Fichier écrit." in out
    assert "NON VÉRIFIÉ" in out
    assert "ne conclus pas" in out.lower()


def test_un_fichier_valide_n_alarme_PAS(tmp_path):
    """AUD-017 — désaturation des gardes : un avertissement permanent ne serait
    plus lu."""
    from src.reasoning.handlers.files import _append_syntax_warning
    cible = _ecrit(tmp_path, "ok.py", "x = 1\n")
    assert asyncio.run(_append_syntax_warning("Écrit.", cible)) == "Écrit."


def test_un_fichier_ABSENT_ne_declenche_pas_la_note(tmp_path):
    """Un fichier manquant est une AUTRE anomalie que « langage non vérifiable ».
    Les confondre brouillerait le signal — et le contrat historique de
    `_append_syntax_warning` (ne jamais lever, ne pas polluer) est préservé."""
    from src.reasoning.handlers.files import _append_syntax_warning
    assert asyncio.run(
        _append_syntax_warning("ok", tmp_path / "ghost.py")) == "ok"


def test_une_ERREUR_reste_prioritaire_sur_la_note(tmp_path):
    from src.reasoning.handlers.files import _append_syntax_warning
    cible = _ecrit(tmp_path, "ko.py", "def f(:\n")
    out = asyncio.run(_append_syntax_warning("Écrit.", cible))
    assert "Syntaxe/lint" in out
    assert "NON VÉRIFIÉ" not in out


# ══════════════════════════════════════════════════════════════════════════
#  5. LE CODEAGENT NE BLOQUE PAS — IL DIT (doctrine Z40c)
# ══════════════════════════════════════════════════════════════════════════


def test_le_codeagent_codex_utilise_les_TROIS_etats():
    """GARDE STRUCTURELLE. La porte de validation de `codex_codeagent` refusait sur
    `syntax_errors` et ignorait tout le reste. Elle doit désormais distinguer
    l'erreur (refus) du non-vérifiable (constat)."""
    src = Path("src/llm/codex_codeagent.py").read_text(encoding="utf-8")
    assert "verify_syntax" in src
    assert "await check_syntax(" not in src        # plus d'appel aveugle
    assert "non_verifies" in src
    assert "syntax_unverified" in src              # interrogeable au ledger


def test_un_langage_non_verifiable_ne_FAIT_PAS_echouer():
    """Bloquer un projet Rust parce que la machine n'a pas `rustc` serait absurde.
    Doctrine Z40c : la porte laisse TOUJOURS passer, elle ne se tait pas.

    On lit le code parce que la porte vit dans un flux Codex complet, non
    instanciable en test unitaire : seul `syntax_errors` doit conduire au refus."""
    src = Path("src/llm/codex_codeagent.py").read_text(encoding="utf-8")
    i = src.index("if syntax_errors or (tests_expected and not green_test):")
    condition = src[i:i + 120]
    assert "non_verifies" not in condition, (
        "un fichier non vérifiable ne doit jamais faire échouer la tâche"
    )


def test_le_constat_est_JOINT_a_la_sortie_du_codeagent():
    """Le worker lit cette sortie : c'est par là que le fait l'atteint."""
    src = Path("src/llm/codex_codeagent.py").read_text(encoding="utf-8")
    i = src.index("if non_verifies:")
    bloc = src[i:i + 700]
    assert "NON VÉRIFIÉ" in bloc
    assert "_sortie" in bloc
    assert "pas été prouvé correct" in bloc
