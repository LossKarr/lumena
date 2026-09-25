"""LOT 8bc — « JE N'AI PAS PU VÉRIFIER » NE DOIT PAS VOULOIR DIRE « JE N'AI PAS SU CHERCHER ».

═══════════════════════════════════════════════════════════════════════════════
  CE QUE LE RUN RÉEL DU 02/09 A MONTRÉ
═══════════════════════════════════════════════════════════════════════════════

Mission BudgetBuddy (PHP). La mission a cherché l'interpréteur ainsi :

    where php                    →  absent du PATH
    recherche XAMPP/WAMP         →  la commande PowerShell PLANTE
                                    (`out-file : FileStream devait ouvrir…`)
    docker run                   →  daemon arrêté
    conclusion                   →  « PHP n'est pas installé sur cette machine »

Six minutes plus tard, à la demande de l'utilisateur, elle testait quatre chemins
avec `Test-Path` et trouvait `C:\\php\\php.exe` — **PHP 8.5.1, installé**.

Le lot 8a distinguait « validé » de « pas pu vérifier ». Celui-ci va un cran plus
loin : **une recherche ratée n'est pas une absence.**

═══════════════════════════════════════════════════════════════════════════════
  LA DÉCISION (utilisateur, 2026-09-03)
═══════════════════════════════════════════════════════════════════════════════

    « il faudra les deux — test en local si docker non disponible, et docker
      si disponible ; il faut toujours qu'elle fasse »

Donc : **local d'abord** (PATH *puis* emplacements courants), **Docker ensuite**,
et `non_verifiable` seulement après avoir tenté les deux — en DISANT lesquelles.

═══════════════════════════════════════════════════════════════════════════════
  LA CIBLE, MESURÉE
═══════════════════════════════════════════════════════════════════════════════

`workspace/`, hors node_modules / .git / venv / .backups :

    87 fichiers de code non validables
       63 .php  (72 %)   ← l'essentiel du gisement
       11 .sql  (13 %)
        8 .cs   ( 9 %)
        4 .lua  ( 5 %)
        1 .ps1

`.sql`, `.cs`, `.rs`, `.java` ne sont PAS dans la table : ils demandent un projet
complet, pas un fichier isolé. Les y mettre mentirait sur ce qu'on vérifie.

═══════════════════════════════════════════════════════════════════════════════
  PORTABILITÉ
═══════════════════════════════════════════════════════════════════════════════

Ces tests doivent passer sur une machine SANS php, SANS ruby et SANS Docker —
donc en CI. Tout ce qui dépend d'un outil réel est soit simulé, soit `skipif`.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest

from src.utils import syntax_check as sc


@pytest.fixture(autouse=True)
def _caches_propres():
    """Les caches sont un état de module : un test ne doit jamais hériter de l'autre."""
    sc.reset_caches_pour_tests()
    yield
    sc.reset_caches_pour_tests()


def _ecrit(tmp_path: Path, nom: str, contenu: str) -> Path:
    p = tmp_path / nom
    p.write_text(contenu, encoding="utf-8")
    return p


# ══════════════════════════════════════════════════════════════════════════
#  1. LE DÉFAUT DU RUN — chercher AILLEURS que dans le PATH
# ══════════════════════════════════════════════════════════════════════════


def test_un_outil_HORS_PATH_est_trouve(tmp_path, monkeypatch):
    """LE cœur du lot. `C:\\php\\php.exe` existait et n'a pas été vu, parce que
    personne n'a regardé ailleurs que dans le PATH."""
    faux = tmp_path / "php.exe"
    faux.write_text("", encoding="utf-8")
    monkeypatch.setattr(sc.shutil, "which", lambda _: None)      # absent du PATH
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", (str(faux),))
    assert sc.trouver_outil("php") == str(faux)


def test_le_PATH_reste_prioritaire(tmp_path, monkeypatch):
    """Un outil du PATH est celui que l'utilisateur a choisi : il passe devant."""
    monkeypatch.setattr(sc.shutil, "which", lambda n: "/usr/bin/php")
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", (r"C:\php\php.exe",))
    assert sc.trouver_outil("php") == "/usr/bin/php"


def test_le_lanceur_WSL_ne_masque_pas_git_bash(tmp_path, monkeypatch):
    """System32/bash.exe est un pont WSL, pas un validateur de chemins Windows."""
    git_bash = tmp_path / "bash.exe"
    git_bash.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        sc.shutil, "which", lambda _: r"C:\Windows\System32\bash.exe"
    )
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "bash", (str(git_bash),))

    assert sc.trouver_outil("bash") == str(git_bash)


def test_un_outil_VRAIMENT_absent_rend_une_chaine_vide(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda _: None)
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", ("/chemin/qui/n/existe/pas",))
    assert sc.trouver_outil("php") == ""


def test_la_recherche_est_MISE_EN_CACHE(monkeypatch):
    """Sonder le disque à chaque fichier écrit coûterait plus cher que la
    vérification elle-même."""
    appels = []
    monkeypatch.setattr(sc.shutil, "which", lambda n: appels.append(n) or None)
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", ())
    sc.trouver_outil("php")
    sc.trouver_outil("php")
    sc.trouver_outil("php")
    assert len(appels) == 1


def test_les_emplacements_courants_couvrent_le_cas_REEL():
    """`C:\\php\\php.exe` est le chemin exact que la mission n'a pas trouvé."""
    chemins = sc._CHEMINS_COURANTS["php"]
    assert r"C:\php\php.exe" in chemins
    for attendu in ("xampp", "laragon", "wamp"):
        assert any(attendu in c.lower() for c in chemins), attendu


# ══════════════════════════════════════════════════════════════════════════
#  2. DOCKER — le binaire ne suffit pas, c'est le DAEMON qui compte
# ══════════════════════════════════════════════════════════════════════════


def test_docker_installe_mais_daemon_ARRETE_compte_comme_indisponible(monkeypatch):
    """Au run du 02/09, `docker --version` répondait « 29.3.1 » et le daemon était
    mort : `docker run` a échoué. C'est `docker info` qui tranche."""
    monkeypatch.setattr(sc.shutil, "which", lambda n: "/usr/bin/docker")

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "failed to connect to the docker API"

    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: _Proc())
    assert sc.docker_disponible() is False


def test_docker_absent_ne_leve_pas(monkeypatch):
    monkeypatch.setattr(sc.shutil, "which", lambda _: None)
    assert sc.docker_disponible() is False


def test_l_etat_docker_est_MIS_EN_CACHE(monkeypatch):
    """`docker info` prend jusqu'à 12 s : une fois par run, pas par fichier."""
    appels = []

    class _Proc:
        returncode = 0
        stdout = "27.0.0"
        stderr = ""

    monkeypatch.setattr(sc.shutil, "which", lambda n: "/usr/bin/docker")
    monkeypatch.setattr(sc.subprocess, "run",
                        lambda *a, **k: appels.append(1) or _Proc())
    assert sc.docker_disponible() is True
    sc.docker_disponible()
    sc.docker_disponible()
    assert len(appels) == 1


# ══════════════════════════════════════════════════════════════════════════
#  3. LES DEUX VOIES, DANS L'ORDRE — et le verdict le DIT
# ══════════════════════════════════════════════════════════════════════════


def test_ni_local_ni_docker_le_verdict_NOMME_les_deux_voies(tmp_path, monkeypatch):
    """C'est ce qui manquait au run : « PHP n'est pas installé » était faux, et
    surtout ne disait pas OÙ on avait cherché."""
    monkeypatch.setattr(sc.shutil, "which", lambda _: None)
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", ())
    monkeypatch.setattr(sc, "docker_disponible", lambda: False)
    verdict, detail = asyncio.run(
        sc.verify_syntax(_ecrit(tmp_path, "x.php", "<?php echo 1;")))
    assert verdict == sc.NON_VERIFIABLE
    assert "PATH et emplacements courants" in detail
    assert "Docker indisponible" in detail


def test_docker_est_tente_QUAND_le_local_manque(tmp_path, monkeypatch):
    """« docker si disponible ; il faut toujours qu'elle fasse »."""
    vus = []

    def _faux_run(cmd, *a, **k):
        vus.append(cmd)

        class _P:
            returncode = 0
            stdout = ""
            stderr = ""
        return _P()

    monkeypatch.setattr(sc.shutil, "which", lambda n: None)
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", ())
    monkeypatch.setattr(sc, "docker_disponible", lambda: True)
    monkeypatch.setattr(sc.subprocess, "run", _faux_run)
    verdict, _ = asyncio.run(
        sc.verify_syntax(_ecrit(tmp_path, "x.php", "<?php echo 1;")))
    assert verdict == sc.OK
    assert vus and vus[0][0] == "docker"
    assert "php:8.3-cli" in vus[0]
    assert "-l" in vus[0]


def test_le_conteneur_est_SANS_RESEAU(tmp_path, monkeypatch):
    """On vérifie une syntaxe, pas plus. Un fichier écrit par un modèle ne doit
    pas pouvoir sortir sur le réseau parce qu'on le valide."""
    vus = []
    monkeypatch.setattr(sc.shutil, "which", lambda n: None)
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", ())
    monkeypatch.setattr(sc, "docker_disponible", lambda: True)

    class _P:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(sc.subprocess, "run", lambda cmd, *a, **k: vus.append(cmd) or _P())
    asyncio.run(sc.verify_syntax(_ecrit(tmp_path, "x.php", "<?php echo 1;")))
    assert "--network" in vus[0] and "none" in vus[0]
    assert "--rm" in vus[0]


def test_le_local_passe_AVANT_docker(tmp_path, monkeypatch):
    """L'outil de la machine est plus rapide et ne télécharge rien."""
    faux = tmp_path / "php.exe"
    faux.write_text("", encoding="utf-8")
    vus = []

    class _P:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(sc.shutil, "which", lambda _: None)
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", (str(faux),))
    monkeypatch.setattr(sc, "docker_disponible", lambda: True)
    monkeypatch.setattr(sc.subprocess, "run", lambda cmd, *a, **k: vus.append(cmd) or _P())
    asyncio.run(sc.verify_syntax(_ecrit(tmp_path, "x.php", "<?php echo 1;")))
    assert vus[0][0] == str(faux)          # l'outil local, pas docker
    assert not any(c and c[0] == "docker" for c in vus)


def test_un_outil_trouve_mais_INEXECUTABLE_ne_ment_pas(tmp_path, monkeypatch):
    """Trouvé ≠ fonctionnel. Un binaire corrompu ou un timeout ne valide rien —
    et ne condamne rien non plus."""
    faux = tmp_path / "php.exe"
    faux.write_text("", encoding="utf-8")
    monkeypatch.setattr(sc.shutil, "which", lambda _: None)
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", (str(faux),))

    def _boum(*a, **k):
        raise OSError("binaire illisible")

    monkeypatch.setattr(sc.subprocess, "run", _boum)
    verdict, detail = asyncio.run(
        sc.verify_syntax(_ecrit(tmp_path, "x.php", "<?php echo 1;")))
    assert verdict == sc.NON_VERIFIABLE
    assert "trouvé mais inexécutable" in detail


# ══════════════════════════════════════════════════════════════════════════
#  4. LA TABLE — ce qu'on promet, et ce qu'on ne promet pas
# ══════════════════════════════════════════════════════════════════════════


def test_php_est_dans_la_table():
    """63 des 87 fichiers non validés du workspace (72 %)."""
    assert ".php" in sc._VALIDATEURS
    outil, args, image = sc._VALIDATEURS[".php"]
    assert (outil, args) == ("php", ["-l"])
    assert image


@pytest.mark.parametrize("ext", [".sql", ".cs", ".rs", ".java"])
def test_les_langages_a_PROJET_ne_sont_PAS_promis(ext):
    """`sqlfluff`, `dotnet build`, `rustc`, `javac` ont besoin d'un projet, pas
    d'un fichier isolé. Les mettre dans la table ferait croire à une validation
    qui n'en serait pas une — exactement le mensonge que le lot 8a a supprimé."""
    assert ext not in sc._VALIDATEURS


def test_chaque_entree_de_la_table_est_BIEN_FORMEE():
    for ext, valeur in sc._VALIDATEURS.items():
        assert ext.startswith("."), ext
        outil, args, image = valeur
        assert outil and isinstance(args, list) and args, ext
        assert isinstance(image, str), ext          # "" = pas d'image, c'est permis


# ══════════════════════════════════════════════════════════════════════════
#  5. NON-RÉGRESSION DU LOT 8a
# ══════════════════════════════════════════════════════════════════════════


def test_les_cinq_familles_historiques_sont_INTACTES(tmp_path):
    for nom, contenu, attendu in [
        ("ok.py", "x = 1\n", sc.OK),
        ("ko.py", "def f(:\n", sc.ERREUR),
        ("ok.json", '{"a": 1}', sc.OK),
        ("ko.json", "{bad", sc.ERREUR),
        ("ok.css", "a{color:red}", sc.OK),
    ]:
        verdict, _ = asyncio.run(sc.verify_syntax(_ecrit(tmp_path, nom, contenu)))
        assert verdict == attendu, nom


def test_un_format_hors_table_garde_son_message(tmp_path):
    verdict, detail = asyncio.run(
        sc.verify_syntax(_ecrit(tmp_path, "notes.md", "# titre")))
    assert verdict == sc.NON_VERIFIABLE
    assert "aucun validateur pour ce format" in detail


def test_check_syntax_garde_son_contrat(tmp_path, monkeypatch):
    """Ses deux appelants attendent une `str` : "" = rien à signaler."""
    monkeypatch.setattr(sc.shutil, "which", lambda _: None)
    monkeypatch.setitem(sc._CHEMINS_COURANTS, "php", ())
    monkeypatch.setattr(sc, "docker_disponible", lambda: False)
    out = asyncio.run(sc.check_syntax(_ecrit(tmp_path, "x.php", "<?php echo 1;")))
    assert out == ""          # non vérifiable ≠ erreur


# ══════════════════════════════════════════════════════════════════════════
#  6. AVEC UN VRAI OUTIL — ignoré si la machine ne l'a pas (CI)
# ══════════════════════════════════════════════════════════════════════════


def _php_reel():
    sc.reset_caches_pour_tests()
    return sc.trouver_outil("php")


@pytest.mark.skipif(not _php_reel(), reason="aucun interpréteur PHP sur cette machine")
def test_php_REEL_valide_et_rejette(tmp_path):
    """Sur la machine du 02/09, `C:\\php\\php.exe` (PHP 8.5.1) existe hors PATH :
    c'est exactement le fichier que la mission a déclaré invérifiable."""
    bon = asyncio.run(sc.verify_syntax(
        _ecrit(tmp_path, "bon.php", "<?php function f() { return 1; }")))
    assert bon[0] == sc.OK

    ko = asyncio.run(sc.verify_syntax(
        _ecrit(tmp_path, "ko.php", "<?php function f( { return 1; }")))
    assert ko[0] == sc.ERREUR
    assert "syntax error" in ko[1].lower() or "parse error" in ko[1].lower()


@pytest.mark.skipif(not shutil.which("bash"), reason="pas de bash")
def test_bash_REEL_valide_et_rejette(tmp_path):
    assert asyncio.run(sc.verify_syntax(
        _ecrit(tmp_path, "ok.sh", "echo ok\nif true; then echo x; fi")))[0] == sc.OK
    assert asyncio.run(sc.verify_syntax(
        _ecrit(tmp_path, "ko.sh", "if true; then echo x")))[0] == sc.ERREUR
