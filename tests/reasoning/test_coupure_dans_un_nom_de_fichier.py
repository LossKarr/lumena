"""LA COUPE DE PHRASE NE TOMBE PLUS DANS UN NOM DE FICHIER.

═══════════════════════════════════════════════════════════════════════════════
  CE QUI A ÉTÉ SERVI À L'UTILISATEUR
═══════════════════════════════════════════════════════════════════════════════

Log réel du 02/09. Le garde Z29 détecte correctement une fuite de pensée
(`cas=prefixe_reflexion len=364`), puis le nettoyage automatique la SERT quand
même, en annonçant « économise une reformulation ». Reçu à l'écran :

    mp4/` pour trouver le fichier vidéo réel. […] L'utilisateur veut un
    « dossier » en HTML […] Je vais lister le contenu du dossier vidéo…

Du raisonnement interne, commençant au milieu d'un mot, qui parle de
l'utilisateur à la troisième personne — à l'utilisateur.

═══════════════════════════════════════════════════════════════════════════════
  LES DEUX CAUSES, QUI SE COMPOSENT
═══════════════════════════════════════════════════════════════════════════════

**La coupe.** La frontière de phrase était un point NU : `[^.!?\\n]{0,200}
[.!?\\n]`. Un nom de fichier en contient un. `video.mp4` a été coupé dans son
extension — d'où le `mp4/` initial.

**L'acceptation.** Le résultat n'était vérifié que par sa LONGUEUR (`>= 50`).
Jamais par sa nature. Ce qui restait était pourtant du raisonnement pur.

═══════════════════════════════════════════════════════════════════════════════
  CE QUI A ÉTÉ MESURÉ AVANT D'ÉCRIRE UNE LIGNE
═══════════════════════════════════════════════════════════════════════════════

Corpus réel `data/training_pool/`, **1707 réponses d'assistant** :

    critère « motif de réflexion n'importe où »   372/1707 = 21,8 %  ÉCARTÉ
    critère « commence par minuscule OU symbole » 256/1707 = 15,0 %  ÉCARTÉ
    critère « commence par une lettre `Ll` »        4/1707 =  0,23 % RETENU

Les QUATRE déclenchements du critère retenu sont quatre vraies fuites, toutes
de la même forme — la coupe est tombée dans une extension de fichier :

    mp4/` pour trouver le fichier vidéo réel…      (video.mp4)
    js`   pour voir l'état actuel du code…         (script.js)
    js    pour comment canCraft est appelé…        (*.js)
    py.   Le problème est la fonction…             (*.py)

Zéro faux positif. Le défaut durait depuis des mois : le test
`test_strips_french_user_prefix` passait même dessus, parce que son fixture
contient `main.py` et que ses assertions ne regardaient pas le début du
résultat.

APRÈS correctif, les mêmes 1707 réponses repassées dans la fonction :
23 sont réellement nettoyées (le nettoyage reste actif) et **0** produit un
résultat commençant par une minuscule.

═══════════════════════════════════════════════════════════════════════════════
  POURQUOI DEUX LOTS ET PAS UN
═══════════════════════════════════════════════════════════════════════════════

Le lot 1 supprime la cause. Le lot 2 est le filet : si un motif futur coupe mal,
le texte n'est JAMAIS servi — la fonction rend `None` et la reformulation
habituelle reprend la main. Le coût d'une erreur est asymétrique : rendre `None`
ne perd rien, servir le texte met le raisonnement sous les yeux de
l'utilisateur.
"""

from __future__ import annotations

import glob
import json
import re
import unicodedata
from pathlib import Path

import pytest

from src.reasoning import final_guards as fg

_ROOT = Path(__file__).parents[2]


# ══════════════════════════════════════════════════════════════════════════
#  1. LES QUATRE FUITES DU CORPUS SE COUPENT MAINTENANT AU BON ENDROIT
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("texte,debut_attendu", [
    # Le cas exact du log — `video.mp4`
    ("Je dois vérifier le contenu du dossier video.mp4/ pour trouver le fichier "
     "vidéo réel. Ensuite je créerai le dossier HTML avec la vidéo intégrée.",
     "Ensuite je créerai"),
    # `script.js`
    ("Je vais lire script.js pour voir l'état actuel du code. L'erreur vient de "
     "là, dans la fonction de rendu, et je la corrige tout de suite.",
     "L'erreur vient de là"),
    # `parser.py`
    ("Je dois regarder parser.py. Le problème est la fonction _parse_tree_bits "
     "qui casse le rendu de la page.",
     "Le problème est la fonction"),
    # `main.py` — le fixture du test historique qui passait sur une coupe cassée
    ("L'utilisateur demande de corriger le bug dans le fichier main.py. Voici la "
     "correction appliquée : le fichier a été modifié avec succès.",
     "Voici la correction"),
])
def test_la_coupe_ne_tombe_plus_dans_l_extension(texte, debut_attendu):
    out = fg.strip_thought_leak_prefix(texte)
    assert out is not None, "la coupe a été refusée alors qu'elle est propre"
    assert out.startswith(debut_attendu), repr(out[:60])


def test_un_point_COLLE_a_une_lettre_n_est_pas_une_fin_de_phrase():
    """C'est toute la correction, en une assertion."""
    out = fg.strip_thought_leak_prefix(
        "Je vais ouvrir index.html puis style.css et enfin app.js. La suite du "
        "message est la vraie réponse utilisateur, bien assez longue."
    )
    assert out is not None
    assert out.startswith("La suite du message")


def test_un_point_SUIVI_d_un_blanc_reste_une_fin_de_phrase():
    """On corrige la coupe, on ne la supprime pas."""
    out = fg.strip_thought_leak_prefix(
        "Je vais faire ceci. Et voici enfin la réponse destinée à l'utilisateur, "
        "suffisamment longue pour passer le plancher."
    )
    assert out is not None and out.startswith("Et voici enfin")


# ══════════════════════════════════════════════════════════════════════════
#  2. LE FILET — une coupe au milieu d'un mot n'est jamais servie
# ══════════════════════════════════════════════════════════════════════════


def test_un_reste_commencant_par_une_MINUSCULE_est_refuse():
    """Le filet du lot 2. Même si un motif futur coupe mal, rien ne sort.

    Rendre None ne perd rien : la reformulation habituelle reprend la main."""
    assert fg.strip_thought_leak_prefix(
        "Je dois regarder ce\nmp4/ pour trouver le fichier vidéo réel et ensuite "
        "créer le dossier HTML avec la vidéo intégrée dedans."
    ) is None


@pytest.mark.parametrize("debut", ["Voici", "⚠️ Attention", "**Résumé**", "1. D'abord",
                                   "- Premier point", "`code`", "« Citation",
                                   "✅ C'est fait"])
def test_les_vrais_debuts_de_reponse_PASSENT(debut):
    """Le critère « minuscule OU symbole » donnait 15 % de faux positifs :
    emoji, puces, gras et ponctuation ouvrent légitimement une réponse. Seule
    la catégorie Unicode `Ll` sépare proprement."""
    texte = ("Je vais te répondre maintenant. " + debut
             + " la réponse réelle destinée à l'utilisateur, assez longue pour "
               "franchir le plancher de cinquante caractères.")
    out = fg.strip_thought_leak_prefix(texte)
    assert out is not None, f"début légitime refusé : {debut!r}"
    assert out.startswith(debut)


def test_le_filet_regarde_la_CATEGORIE_unicode_pas_l_ascii():
    """Une minuscule accentuée est une minuscule ; un emoji n'en est pas une."""
    assert unicodedata.category("é") == "Ll"
    assert unicodedata.category("⚠") != "Ll"
    assert unicodedata.category("1") != "Ll"
    assert unicodedata.category("`") != "Ll"


# ══════════════════════════════════════════════════════════════════════════
#  3. CE QUI NE DOIT PAS AVOIR CHANGÉ
# ══════════════════════════════════════════════════════════════════════════


def test_le_nettoyage_fonctionne_TOUJOURS():
    """On corrige un défaut, on ne neutralise pas la fonction."""
    out = fg.strip_thought_leak_prefix(
        "L'utilisateur demande une synthèse. Voici la synthèse demandée, avec "
        "assez de matière pour dépasser le plancher de cinquante caractères."
    )
    assert out is not None and out.startswith("Voici la synthèse")


def test_le_plancher_de_longueur_est_INTACT():
    """Règle préexistante : sous 50 caractères utiles, on abandonne."""
    assert fg.strip_thought_leak_prefix("Je vais faire ceci. Trop court.") is None


def test_un_reste_ENCORE_interne_est_toujours_refuse():
    """Le garde `_STILL_INTERNAL` préexistant n'a pas bougé."""
    assert fg.strip_thought_leak_prefix(
        "Je vais commencer. Je dois maintenant vérifier le contenu du fichier "
        "avant de pouvoir répondre correctement à la question posée."
    ) is None


def test_un_texte_SANS_prefixe_interne_traverse_sans_dommage():
    texte = ("Voici directement la réponse à ta question, sans aucune "
             "délibération interne devant elle.")
    assert fg.strip_thought_leak_prefix(texte) == texte


def test_les_NEUF_motifs_portent_la_borne_corrigee():
    """Corriger huit motifs sur neuf laisserait le défaut vivant."""
    src = Path(fg.__file__).read_text(encoding="utf-8")
    i = src.index("_STRIP_PATTERNS = [")
    bloc = src[i:src.index("]", src.index("re.compile", i))]
    bloc = src[i:i + 3000]
    assert bloc.count(r"(?:[^.!?\n]|\.(?=\S))") == 9, "des motifs gardent la borne nue"
    assert r"[^.!?\n]{0," not in bloc, "une borne fautive subsiste"


# ══════════════════════════════════════════════════════════════════════════
#  4. LE CORPUS RÉEL — la mesure qui a déclenché le lot, et sa preuve
# ══════════════════════════════════════════════════════════════════════════


def _reponses_reelles():
    out = []
    for f in glob.glob(str(_ROOT / "data" / "training_pool" / "**" / "*.jsonl"),
                       recursive=True):
        try:
            lignes = Path(f).read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue
        for l in lignes:
            if not l.strip():
                continue
            try:
                d = json.loads(l)
            except Exception:
                continue
            for c in (d.get("conversations") or []):
                if isinstance(c, dict) and c.get("role") == "assistant":
                    v = c.get("content")
                    if isinstance(v, str) and len(v.strip()) >= 50:
                        out.append(v.strip())
    return out


@pytest.mark.skipif(not (_ROOT / "data" / "training_pool").is_dir(),
                    reason="corpus absent de cette machine")
def test_AUCUNE_reponse_du_corpus_ne_sort_coupee_au_milieu_d_un_mot():
    """La preuve du correctif. Avant : quatre. Après : zéro."""
    reps = _reponses_reelles()
    assert len(reps) > 500, f"corpus trop maigre pour conclure : {len(reps)}"
    fautives = []
    for r in reps:
        out = fg.strip_thought_leak_prefix(r)
        if out and out != r and unicodedata.category(out[0]) == "Ll":
            fautives.append(out[:70])
    assert not fautives, f"{len(fautives)} coupes au milieu d'un mot : {fautives[:3]}"


@pytest.mark.skipif(not (_ROOT / "data" / "training_pool").is_dir(),
                    reason="corpus absent de cette machine")
def test_le_nettoyage_reste_ACTIF_sur_le_corpus():
    """Un correctif qui rendrait la fonction inerte passerait le test
    ci-dessus les doigts dans le nez. On vérifie qu'elle travaille encore."""
    reps = _reponses_reelles()
    nettoyees = sum(1 for r in reps
                    if (o := fg.strip_thought_leak_prefix(r)) and o != r)
    assert nettoyees >= 10, f"seulement {nettoyees} nettoyages : fonction inerte ?"
