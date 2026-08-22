"""LOT Q — dire quelle part du HTML publié est réellement stylée.

Run Fibrance (2026-08-14). La mission a tout fait dans les règles : contrat,
3 workers, CodeAgent, `serve_website`, navigation, **clic sur le bouton de
thème**, vérification du DOM, publication. Chaque garde a dit oui. Et la page
livrée est inutilisable — menu en puces, liens bleus soulignés, contenu qui
déborde de l'écran.

Cause immédiate : le HTML écrivait `.nav-menu`, `.cards`, `.gallery`,
`.reveal` ; le CSS stylait `.nav-links`, `.skill-card`, `.gallery-grid`,
`.fade-in`. **12 classes sur 15 ne se rencontraient jamais.**

⚠️ CE QUE L'AUDIT A RÉFUTÉ. J'ai d'abord cru que la cause était le découpage
(HTML et CSS chez deux workers différents) : mes 4 premiers cas étaient tous
cassés. Sur le corpus COMPLET — 135 contrats, 72 avec HTML+CSS — le signal
disparaît :

    owners séparés   6 projets réels  -> 3 à 100 %, 3 sous 20 %
    owner commun    35 projets        -> 13 sous 50 % (37 %)

Un seul propriétaire ne protège de rien (14 %, 25 %, 38 %…), et la séparation
n'empêche pas la perfection (converto, memogame, palindrotest à 100 %). Aucune
règle de découpage n'est justifiée par les données : **je n'en pose donc
aucune.**

Ce qui reste vrai, et qui est le vrai défaut : sur 47 projets web mesurables,
**19 sont sous 50 % de classes stylées** — et ce chiffre n'a jamais été
calculé, jamais dit, jamais porté à la connaissance de qui que ce soit. Le
motif du chantier, encore : un fait vérifiable sur le disque, absent de la
décision. Ici il n'était même pas mesuré.

Ce module ne bloque rien et ne juge personne : il compte, et rend une phrase.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# `class="a b c"` et `class='a b c'` — on ignore les valeurs dynamiques
# (`class="{{ ... }}"` des templates) : elles ne sont pas des noms de classe.
_HTML_CLASS_RE = re.compile(r"""class\s*=\s*["']([^"'{}]+)["']""", re.IGNORECASE)

# Un sélecteur de classe en CSS. On ne cherche pas à parser le CSS : on veut
# savoir si le NOM apparaît comme cible, ce qui suffit à répondre « stylée ou
# pas ». Volontairement permissif — mieux vaut sous-estimer le problème que
# crier au loup.
_CSS_CLASS_RE = re.compile(r"\.(-?[_A-Za-z][\w-]*)")

_HTML_SUFFIXES = (".html", ".htm")
_MIN_CLASSES = 3  # sous ce seuil, le pourcentage ne veut rien dire

# LOT Z8 — dossiers de service : jamais des livrables.
_DOSSIERS_TECHNIQUES: frozenset = frozenset(
    {"__pycache__", "node_modules", ".git", ".backups", ".pytest_cache", ".venv", "venv"}
)


def _safe_root(root: Any) -> Path | None:
    """Un dossier réel, ou rien.

    ⚠️ `Path("")` vaut `Path(".")` — donc un dossier VALIDE, le répertoire
    courant. Un `rglob` dessus balaye le dépôt entier : mesuré ici à 47 s pour
    une seule chaîne vide. Même piège que `snapshot_mission_files(None)` plus
    tôt dans la journée (258 321 fichiers parcourus). Une racine vide n'est
    jamais un site à mesurer : on refuse avant de toucher au disque.
    """
    if not isinstance(root, (str, Path)):
        return None
    if not str(root).strip():
        return None
    try:
        base = Path(root)
        return base if base.is_dir() else None
    except Exception:
        return None


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def html_classes(root: Any) -> set:
    """Tous les noms de classe écrits dans le HTML publié."""
    found: set = set()
    base = _safe_root(root)
    if base is None:
        return found
    for suffix in _HTML_SUFFIXES:
        for page in base.rglob(f"*{suffix}"):
            for group in _HTML_CLASS_RE.findall(_read(page)):
                found.update(part for part in group.split() if part)
    return found


def css_classes(root: Any) -> set:
    """Tous les noms de classe ciblés par une feuille de style.

    Le CSS embarqué dans une balise `<style>` compte aussi : une page d'un seul
    fichier est parfaitement légitime, et l'ignorer ferait passer un site
    correct pour un site cassé.
    """
    found: set = set()
    base = _safe_root(root)
    if base is None:
        return found
    for sheet in base.rglob("*.css"):
        found.update(_CSS_CLASS_RE.findall(_read(sheet)))
    for suffix in _HTML_SUFFIXES:
        for page in base.rglob(f"*{suffix}"):
            for bloc in re.findall(
                r"<style[^>]*>(.*?)</style>", _read(page), re.S | re.I
            ):
                found.update(_CSS_CLASS_RE.findall(bloc))
    return found


def publication_perimee(mission_root: Any, published_at: Any) -> list:
    """Les fichiers du dossier de mission modifiés APRÈS la publication.

    LOT Z8 (run Tanière, 2026-08-15). Z7 a fait exactement ce qu'on attendait :
    la mission a lu « `index.html` n'a que 4/8 de ses classes stylées », a
    décidé (« je dois corriger le style manquant »), a délégué au CodeAgent, et
    le CodeAgent a corrigé — `edit_lines styles.css`, 6 occurrences des classes
    manquantes ajoutées. Puis :

        missions/task_42796022…/styles.css  → corrigé ✅
        workspace/taniere/styles.css        → la version d'avant ❌

    La correction est arrivée après `publish_mission_workspace`, et personne ne
    republie. Le motif du chantier déplacé d'un cran : le fait n'est plus perdu
    avant la décision, il est perdu **après l'action**.

    L'historique n'en montrait qu'un cas sur cinq publications — mais Z7 vient
    précisément de rendre ce chemin fréquent : corriger tard devient la norme.

    Rend les chemins relatifs, triés. Vide si rien n'a bougé, si la mission n'a
    jamais publié, ou si la date est inexploitable — jamais d'exception.
    """
    base = _safe_root(mission_root)
    if base is None or not published_at:
        return []
    try:
        from datetime import datetime

        horodatage = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        seuil = horodatage.timestamp()
    except Exception:
        return []
    modifies: list = []
    for fichier in base.rglob("*"):
        try:
            if not fichier.is_file():
                continue
            # Les dossiers techniques ne sont pas des livrables : les citer
            # noierait le vrai fichier à republier sous du bruit interne
            # (`.backups/styles.css.20260815_160229` observé sur Tanière).
            if any(part in _DOSSIERS_TECHNIQUES or part.startswith(".") for part in fichier.parts):
                continue
            if fichier.stat().st_mtime <= seuil:
                continue
            modifies.append(fichier.relative_to(base).as_posix())
        except Exception:
            continue
    return sorted(modifies)


def publication_perimee_note(mission_root: Any, published_at: Any) -> str:
    """La phrase à joindre quand le livrable publié n'est plus à jour.

    Republier coûte UN appel d'outil : contrairement au style, ce constat reste
    actionnable même tard. On le dit donc fort, sans rien bloquer.
    """
    modifies = publication_perimee(mission_root, published_at)
    if not modifies:
        return ""
    listes = ", ".join(f"`{c}`" for c in modifies[:6])
    reste = len(modifies) - 6
    if reste > 0:
        listes += f" (+{reste})"
    return (
        f"\n\n⚠️ **Le livrable publié n'est plus à jour** : {listes} "
        f"{'ont' if len(modifies) > 1 else 'a'} changé depuis la publication. "
        "Ta correction est sur le disque de la mission mais l'utilisateur reçoit "
        "encore la version d'avant → rappelle `publish_mission_workspace` AVANT "
        "de conclure."
    )


def _pages(base: Path) -> list:
    """Les pages HTML du projet, dans un ordre stable."""
    trouve: list = []
    for suffix in _HTML_SUFFIXES:
        trouve.extend(base.rglob(f"*{suffix}"))
    return sorted(trouve, key=lambda p: str(p).lower())


def _sheet_classes(base: Path) -> set:
    """Les classes ciblées par les feuilles `.css` du projet (partagées)."""
    found: set = set()
    for sheet in base.rglob("*.css"):
        found.update(_CSS_CLASS_RE.findall(_read(sheet)))
    return found


def _inline_classes(page: Path) -> set:
    """Les classes ciblées par les `<style>` de CETTE page uniquement.

    Le détail compte : compter le `<style>` des AUTRES pages ferait passer une
    page nue pour habillée — exactement le cas qu'on cherche à voir.
    """
    found: set = set()
    for bloc in re.findall(r"<style[^>]*>(.*?)</style>", _read(page), re.S | re.I):
        found.update(_CSS_CLASS_RE.findall(bloc))
    return found


def style_coverage_by_page(root: Any) -> list:
    """La couverture de style page par page, la plus basse d'abord.

    LOT Z7 (run Palier, 2026-08-15). La mesure globale disait « 14/38 = 37 % ».
    La réalité était `app.html` à **100 %** et `index.html` à **4 %** : deux
    situations opposées, noyées dans une moyenne qui ne décrivait ni l'une ni
    l'autre. Un lead ne peut pas rattraper une page qu'aucun chiffre ne désigne
    — et de fait, sur les deux missions à couverture basse du corpus (40 % et
    37 %), aucune correction n'a jamais été tentée, même avec 24 appels d'outil
    de marge restante.

    Rend une liste de dicts (`page`, `total`, `styled`, `percent`, `unstyled`),
    triée par couverture croissante. Vide quand la question ne se pose pas.
    """
    base = _safe_root(root)
    if base is None:
        return []
    partagees = _sheet_classes(base)
    resultats: list = []
    for page in _pages(base):
        utilisees: set = set()
        for group in _HTML_CLASS_RE.findall(_read(page)):
            utilisees.update(part for part in group.split() if part)
        if len(utilisees) < _MIN_CLASSES:
            continue
        stylees = utilisees & (partagees | _inline_classes(page))
        try:
            nom = page.relative_to(base).as_posix()
        except Exception:
            nom = page.name
        resultats.append(
            {
                "page": nom,
                "total": len(utilisees),
                "styled": len(stylees),
                "percent": round(100 * len(stylees) / len(utilisees)),
                "unstyled": sorted(utilisees - stylees),
            }
        )
    resultats.sort(key=lambda r: (r["percent"], r["page"]))
    return resultats


def style_coverage(root: Any) -> dict | None:
    """Combien des classes du HTML ont réellement une règle CSS ?

    Rend `None` quand la question ne se pose pas : pas de HTML, ou moins de
    trois classes (un pourcentage sur deux classes n'apprend rien).
    """
    used = html_classes(root)
    if len(used) < _MIN_CLASSES:
        return None
    styled = used & css_classes(root)
    orphelines = sorted(used - styled)
    return {
        "total": len(used),
        "styled": len(styled),
        "percent": round(100 * len(styled) / len(used)),
        "unstyled": orphelines,
    }


def style_coverage_note(root: Any) -> str:
    """La phrase à joindre au message de publication. Vide si non pertinent.

    Pas d'interdiction, pas de « tu dois » : un constat chiffré, comme les
    mesures du LOT N. C'est à la mission de décider quoi en faire — mais elle
    ne pourra plus dire qu'elle ne savait pas.
    """
    mesure = style_coverage(root)
    if not mesure:
        return ""
    pct = mesure["percent"]
    # LOT Z7 — le détail par page. Une moyenne ne désigne aucun fichier, donc
    # elle n'appelle aucune action : sur Palier, « 37 % » cachait une page à
    # 100 % et une page à 4 %, et rien n'a été corrigé.
    pages = style_coverage_by_page(root)
    detail = ""
    if len(pages) > 1:
        detail = " Par page : " + " · ".join(
            f"`{p['page']}` {p['percent']} %" for p in pages[:4]
        ) + "."
    if pct >= 80 and (not pages or pages[0]["percent"] >= 80):
        return (
            f"\n🎨 Style : {mesure['styled']}/{mesure['total']} classes du HTML "
            f"ont une règle CSS ({pct} %).{detail}"
        )
    # La page la PLUS mal couverte est celle qu'il faut nommer : c'est elle
    # qu'une moyenne rassurante ferait disparaître.
    pire = pages[0] if pages else None
    if pire is not None and len(pages) > 1:
        exemples = ", ".join(f"`.{c}`" for c in pire["unstyled"][:6])
        reste = len(pire["unstyled"]) - 6
        if reste > 0:
            exemples += f" (+{reste})"
        return (
            f"\n🎨 **Style : `{pire['page']}` n'a que {pire['styled']}/{pire['total']} "
            f"de ses classes stylées ({pire['percent']} %).** Sans règle : {exemples}."
            f"{detail} Une page peut réussir toutes ses interactions et rester "
            "illisible — vérifie le rendu de CHAQUE page, pas seulement de la première."
        )
    exemples = ", ".join(f"`.{c}`" for c in mesure["unstyled"][:6])
    reste = len(mesure["unstyled"]) - 6
    if reste > 0:
        exemples += f" (+{reste})"
    return (
        f"\n🎨 **Style : seulement {mesure['styled']}/{mesure['total']} classes du "
        f"HTML ont une règle CSS ({pct} %).** Sans règle : {exemples}. "
        "Une page peut réussir toutes ses interactions et rester illisible — "
        "vérifie le rendu, pas seulement le comportement."
    )
