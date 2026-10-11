r"""LOT S1 — Lumena sait où elle en est, maintenant.

--- Le défaut, mesuré le 2026-10-09 ---

Dans son prompt, Lumena lit :

    Disque C: — 96.6% utilisé (16.3 Go libres)      ← heartbeat du 17 AOÛT

Et dans `data/ops/metrics.jsonl`, mesuré 15 minutes plus tôt :

    {"disk_percent": 98.8, "disk_free_gb": 5.9, "ram_available_mb": 10965}

**Elle croit avoir 16 Go. Elle en a 5,9.** Elle ne se trompe pas : elle **récite un
souvenir** au lieu de regarder maintenant.

`metrics.jsonl` contient **952 mesures de santé** et **784 sondages de fournisseurs**,
rafraîchis toutes les quelques minutes. **Aucune n'atteint ce que le modèle lit.**

--- Le patron suivi : le bloc Émotion ---

    [Émotion] Humeur=neutral | Énergie=medium | PAD(+0.02,+0.12,+0.15)

67 caractères, frais, structuré, injecté à chaque tour. S1 fait pareil pour le système.

--- Les trois règles non négociables ---

1. **Un FAIT, jamais une consigne.** Le bloc Émotion dit l'état, il ne dit pas « sois
   joyeuse ». S1 ne doit contenir aucune instruction de comportement — sinon Lumena
   devient celle qui rappelle ton disque à chaque message.
2. **Jamais bloquant.** Fichier absent, corrompu, illisible → chaîne vide, zéro exception.
3. **Jamais d'invention.** Une mesure de plus d'une heure DIT son âge. Mieux vaut
   « état inconnu » qu'un chiffre faux — c'est la règle de vérité du produit.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from src.telemetry.etat_systeme import bloc_etat_systeme


def _ecrire(fichier, entrees):
    fichier.write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in entrees),
        encoding="utf-8",
    )
    return fichier


def _sante(ts=None, disk=98.8, libre=5.9, ram=10965, retard=8, alerts=None):
    return {
        "timestamp": (ts or datetime.now()).isoformat(),
        "handler": "runtime_health",
        "data": {
            "status": "warning",
            "checks": {
                "ram_percent": 66.4, "ram_available_mb": ram,
                "disk_percent": disk, "disk_free_gb": libre,
                "uptime_hours": 12.0, "scheduler_overdue": retard,
            },
            "alerts": alerts if alerts is not None else ["Disque critique"],
        },
    }


def _providers(ts=None, **etats):
    return {
        "timestamp": (ts or datetime.now()).isoformat(),
        "handler": "provider_probe",
        "data": {"providers": {
            n: {"healthy": h, "failures": 0, "in_cooldown": not h}
            for n, h in etats.items()
        }},
    }


# ── 1. Le cas qui fonde le lot ───────────────────────────────────────────────


class TestElleLitLaMesureFraiche:
    def test_le_disque_reel_apparait(self, tmp_path):
        """LE défaut : elle annonçait 16,3 Go alors qu'elle en a 5,9."""
        f = _ecrire(tmp_path / "m.jsonl", [_sante()])
        bloc = bloc_etat_systeme(f)
        assert "98" in bloc and "5,9" in bloc
        assert "16" not in bloc, "l'ancien chiffre du 17 août ne doit plus apparaître"

    def test_c_est_la_DERNIERE_mesure_qui_compte(self, tmp_path):
        """Un historique ne sert à rien : seul l'instant présent est un état."""
        vieux = _sante(ts=datetime.now() - timedelta(days=53), disk=96.6, libre=16.3)
        f = _ecrire(tmp_path / "m.jsonl", [vieux, _sante(disk=98.8, libre=5.9)])
        bloc = bloc_etat_systeme(f)
        assert "5,9" in bloc and "16,3" not in bloc

    def test_la_ram_et_le_retard_sont_dits(self, tmp_path):
        f = _ecrire(tmp_path / "m.jsonl", [_sante(ram=10965, retard=8)])
        bloc = bloc_etat_systeme(f)
        assert "10,7" in bloc, "10965 Mo = 10,7 Gio (division par 1024, pas 1000)"
        assert "8" in bloc

    def test_les_fournisseurs_vivants_sont_comptes(self, tmp_path):
        """Elle doit savoir sur quoi elle tourne : 7 à sec sur 8, c'est son plafond."""
        f = _ecrire(tmp_path / "m.jsonl", [
            _sante(),
            _providers(deepseek=True, openai=False, anthropic=False, xai=False),
        ])
        bloc = bloc_etat_systeme(f)
        assert "1/4" in bloc or "3" in bloc  # 1 vivant sur 4, ou 3 à sec


# ── 2. ⚠️ Un FAIT, jamais une consigne ──────────────────────────────────────


class TestAucuneConsigneDeComportement:
    @pytest.mark.parametrize("interdit", [
        "pense à", "n'oublie pas", "tu dois", "préviens", "rappelle",
        "il faut", "attention à", "surveille",
    ])
    def test_le_bloc_ne_donne_aucun_ordre(self, tmp_path, interdit):
        """Le bloc Émotion dit « Humeur=neutral », pas « sois joyeuse ».

        Sans cette règle, Lumena deviendrait celle qui rappelle ton disque à CHAQUE
        message — pire que le silence actuel.
        """
        f = _ecrire(tmp_path / "m.jsonl", [_sante(disk=99.9, libre=0.2)])
        assert interdit not in bloc_etat_systeme(f).lower()

    def test_le_bloc_reste_court(self, tmp_path):
        """Budget de la phase S : ≤ 500 car. pour TOUS les blocs. S1 en prend ≤ 200."""
        f = _ecrire(tmp_path / "m.jsonl", [
            _sante(alerts=["a" * 400, "b" * 400]),
            _providers(**{f"p{i}": False for i in range(12)}),
        ])
        assert len(bloc_etat_systeme(f)) <= 200


# ── 3. Jamais d'invention : l'âge est dit ───────────────────────────────────


class TestFraicheur:
    def test_une_mesure_recente_ne_mentionne_pas_son_age(self, tmp_path):
        f = _ecrire(tmp_path / "m.jsonl", [_sante(ts=datetime.now() - timedelta(minutes=3))])
        assert "il y a" not in bloc_etat_systeme(f).lower()

    @pytest.mark.parametrize("age_h,attendu", [(2, "h"), (30, "h"), (60, "j")])
    def test_une_mesure_ancienne_DIT_son_age(self, tmp_path, age_h, attendu):
        """Mieux vaut un chiffre daté qu'un chiffre présenté comme actuel."""
        f = _ecrire(tmp_path / "m.jsonl", [_sante(ts=datetime.now() - timedelta(hours=age_h))])
        bloc = bloc_etat_systeme(f).lower()
        assert "il y a" in bloc and attendu in bloc

    def test_une_mesure_tres_vieille_n_est_PAS_presentee_comme_un_etat(self, tmp_path):
        """53 jours, c'est l'écart exact du bug d'origine (17 août → 9 octobre)."""
        f = _ecrire(tmp_path / "m.jsonl", [_sante(ts=datetime.now() - timedelta(days=53))])
        bloc = bloc_etat_systeme(f)
        assert bloc == "" or "il y a" in bloc.lower()


# ── 4. Jamais bloquant ───────────────────────────────────────────────────────


class TestJamaisBloquant:
    def test_fichier_absent(self, tmp_path):
        assert bloc_etat_systeme(tmp_path / "jamais_cree.jsonl") == ""

    def test_fichier_vide(self, tmp_path):
        assert bloc_etat_systeme(_ecrire(tmp_path / "m.jsonl", [])) == ""

    def test_lignes_corrompues_ignorees(self, tmp_path):
        f = tmp_path / "m.jsonl"
        f.write_text(
            "{pas du json\n" + json.dumps(_sante(), ensure_ascii=False) + "\n{{{\n",
            encoding="utf-8",
        )
        assert "98" in bloc_etat_systeme(f)

    @pytest.mark.parametrize("entree", [
        {"handler": "runtime_health"},                       # pas de data
        {"handler": "runtime_health", "data": {}},           # pas de checks
        {"handler": "runtime_health", "data": {"checks": None}},
        {"handler": "runtime_health", "data": {"checks": {"disk_percent": "abc"}}},
        {"handler": "autre_chose", "data": {"checks": {}}},  # mauvais handler
    ])
    def test_entrees_malformees(self, tmp_path, entree):
        """Une donnée d'affichage ne doit JAMAIS faire échouer un tour."""
        assert isinstance(bloc_etat_systeme(_ecrire(tmp_path / "m.jsonl", [entree])), str)

    def test_aucun_chemin_absolu_ne_fuit(self, tmp_path):
        """Anti-fuite : le bloc part au LLM, il ne doit porter aucun chemin système."""
        f = _ecrire(tmp_path / "m.jsonl", [_sante(
            alerts=[r"Auto-nettoyage: C:\Users\charl\Desktop\lumena\src\__pycache__"],
        )])
        bloc = bloc_etat_systeme(f)
        assert "C:\\" not in bloc and "/Users/" not in bloc

    def test_le_defaut_lit_le_vrai_fichier_sans_lever(self):
        """Appel sans argument : chemin de production, et il ne doit jamais lever."""
        assert isinstance(bloc_etat_systeme(), str)


# ── 5. Le coût : ce bloc est construit à CHAQUE tour ────────────────────────


class TestLectureEnQueueDeFichier:
    """`metrics.jsonl` pesait **4,0 Mo** le 2026-10-09, et il grossit à chaque sonde.

    Le relire en entier à chaque tour de prompt pour en garder 400 lignes, c'est
    exactement l'« I/O lente » que la phase PRESENCE s'interdit. Découvert en
    branchant le lot, pas en l'écrivant.
    """

    def _gros(self, tmp_path, mo=6):
        f = tmp_path / "m.jsonl"
        bourrage = json.dumps({"handler": "bruit", "data": {"x": "z" * 400}}) + "\n"
        with f.open("w", encoding="utf-8") as fh:
            fh.write(bourrage * int(mo * 1024 * 1024 / len(bourrage)))
            fh.write(json.dumps(_sante(disk=98.8, libre=5.9), ensure_ascii=False) + "\n")
        return f

    def test_la_derniere_mesure_est_lue_dans_un_gros_fichier(self, tmp_path):
        """La queue doit suffire : c'est le seul endroit où vit le présent."""
        assert "5,9" in bloc_etat_systeme(self._gros(tmp_path))

    def test_le_cout_ne_suit_PAS_la_taille_du_fichier(self, tmp_path):
        """6 Mo ne doivent pas coûter 6 Mo de lecture."""
        from src.telemetry.etat_systeme import _MAX_OCTETS

        f = self._gros(tmp_path)
        assert f.stat().st_size > 4 * 1024 * 1024
        assert _MAX_OCTETS <= 512 * 1024, "une queue bornée, sinon le seek ne sert à rien"

    def test_la_ligne_coupee_par_le_seek_ne_casse_rien(self, tmp_path):
        """Se placer au milieu d'une ligne produit du JSON tronqué : il est jeté."""
        assert isinstance(bloc_etat_systeme(self._gros(tmp_path, mo=1)), str)


# ── 6. La voie d'injection, et pourquoi elle passe par react_config ─────────


class TestLeBlocAtteintVraimentLePrompt:
    def test_le_nom_est_exporte_par_react_config(self):
        """Trois gels plafonnent `react.py` à 9 718 lignes (CONN-4d `only_shrink`,
        CONN-5a, CONN-7a). Le nom transite donc par `react_config.py`, déjà importé
        là-bas, sur une ligne d'import EXISTANTE : l'injection coûte ZÉRO ligne.
        Même voie que `observation_lisible` au lot CONN-7a.
        """
        from src.reasoning.react_config import bloc_presence as via_config

        assert callable(via_config)

    def test_l_assemblage_du_prompt_appelle_le_bloc(self):
        """Un bloc que personne n'assemble est un bloc qui n'existe pas — c'est le
        motif des 60 lots : le fait était calculé puis jeté avant le modèle."""
        import pathlib

        source = pathlib.Path("src/reasoning/react.py").read_text(encoding="utf-8")
        assert "bloc_presence(self)" in source, "l'état est calculé mais jamais assemblé"

    def test_un_bloc_vide_n_ajoute_PAS_de_ligne_blanche_au_prompt(self):
        """`join` ne filtrait pas : un état absent aurait creusé un trou dans le prompt."""
        import pathlib

        source = pathlib.Path("src/reasoning/react.py").read_text(encoding="utf-8")
        assert "(*parts, bloc_presence(self)) if p" in source
