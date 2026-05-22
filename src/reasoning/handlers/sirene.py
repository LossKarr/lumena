"""
sirene.py — Handlers V2 SIRENE (recherche-entreprises.api.gouv.fr).

V3.2 (lecture seule) — 2 handlers :
  - sirene_search_company : recherche par nom/SIRET/SIREN/dirigeant
  - sirene_get_by_siret   : lookup direct par SIRET (14 chiffres)

Module séparé de datagouv : host distinct, schéma distinct.
Catégorie : `web` (HTTP public, sans credentials).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


def _get_service():
    from src.services.sirene import get_sirene_service
    return get_sirene_service()


def _format_company_block(c: Dict[str, Any]) -> list[str]:
    """Construit un bloc descriptif d'une entreprise (5-10 lignes)."""
    nom = c.get("nom_complet") or c.get("nom_raison_sociale") or "?"
    siren = c.get("siren", "?")
    siege = c.get("siege") or {}
    siret_siege = siege.get("siret", "?")
    adresse = (
        siege.get("adresse")
        or siege.get("libelle_voie")
        or "?"
    )
    code_postal = siege.get("code_postal", "")
    commune = siege.get("libelle_commune", "")
    nature = c.get("nature_juridique", "?")
    activite = c.get("activite_principale", "?")
    date_creation = c.get("date_creation", "?")
    effectif = c.get("tranche_effectif_salarie") or "non renseigné"
    etat = c.get("etat_administratif") or "?"
    nb_etabs = c.get("nombre_etablissements")
    nb_ouverts = c.get("nombre_etablissements_ouverts")

    lines = [
        f"🏢 **{nom}**",
        f"   SIREN : `{siren}` | SIRET (siège) : `{siret_siege}`",
        f"   Forme juridique : {nature}",
        f"   Activité (NAF) : {activite}",
        f"   Adresse siège : {adresse}".rstrip(),
    ]
    if code_postal or commune:
        lines.append(f"   {code_postal} {commune}".rstrip())
    lines.append(f"   Date création : {date_creation}")
    lines.append(f"   Effectif : {effectif}")
    lines.append(f"   État administratif : {etat}")
    if nb_etabs is not None:
        ouvert_str = f", dont {nb_ouverts} ouvert(s)" if nb_ouverts is not None else ""
        lines.append(f"   Établissements : {nb_etabs}{ouvert_str}")
    # Dirigeants (max 3 affichés)
    dirigeants = c.get("dirigeants") or []
    if dirigeants:
        names = []
        for d in dirigeants[:3]:
            nom_d = d.get("nom_complet") or (
                f"{d.get('prenoms', '')} {d.get('nom', '')}".strip()
            )
            qualite = d.get("qualite") or d.get("fonction") or ""
            label = nom_d
            if qualite:
                label += f" ({qualite})"
            names.append(label)
        if names:
            lines.append(f"   Dirigeants : {' ; '.join(names)}")
    return lines


# ── handler : search_company ────────────────────────────────────────────


async def sirene_search_company_handler(
    ctx: HandlerContext,
    query: str,
    page: int = 1,
    per_page: int = 10,
) -> HandlerResult:
    try:
        svc = _get_service()
        result = await svc.search_companies(query, page=page, per_page=per_page)
        items = result.get("results") or []
        total = result.get("total_results", 0)
        if not items:
            return HandlerResult.ok(
                f"Aucune entreprise trouvée pour '{query}'.\n\n"
                "💡 Conseils :\n"
                "- Vérifier l'orthographe (raison sociale exacte ou approchée)\n"
                "- Essayer un SIREN (9 chiffres) ou SIRET (14 chiffres) si disponible\n"
                "- Recherche par dirigeant possible : nom + prénom",
                handler_name="sirene_search_company",
            )
        lines = [f"🔎 {total} entreprises trouvées (top {len(items)}) pour `{query}` :"]
        for i, c in enumerate(items, 1):
            lines.append(f"\n— Résultat {i} —")
            lines.extend(_format_company_block(c))
        return HandlerResult.ok("\n".join(lines), handler_name="sirene_search_company")
    except Exception as e:
        logger.error(f"sirene_search_company failed: {e}")
        return HandlerResult.fail(
            f"Erreur recherche SIRENE : {e}",
            handler_name="sirene_search_company",
        )


# ── handler : get_by_siret ──────────────────────────────────────────────


async def sirene_get_by_siret_handler(
    ctx: HandlerContext,
    siret: str,
) -> HandlerResult:
    try:
        from src.services.sirene import SireneError
        svc = _get_service()
        try:
            company = await svc.get_company_by_siret(siret)
        except SireneError as se:
            return HandlerResult.fail(
                f"❌ {se}",
                handler_name="sirene_get_by_siret",
            )
        if not company:
            return HandlerResult.ok(
                f"Aucune entreprise trouvée pour SIRET `{siret}` "
                "(soit le SIRET n'existe pas, soit l'entreprise est cessée et purgée).",
                handler_name="sirene_get_by_siret",
            )
        lines = _format_company_block(company)
        return HandlerResult.ok("\n".join(lines), handler_name="sirene_get_by_siret")
    except Exception as e:
        logger.error(f"sirene_get_by_siret failed: {e}")
        return HandlerResult.fail(
            f"Erreur lookup SIRET : {e}",
            handler_name="sirene_get_by_siret",
        )


# ── HandlerDef export ───────────────────────────────────────────────────


def get_sirene_handler_defs() -> list[HandlerDef]:
    """V3.2 : 2 handlers SIRENE. V3.3 ajoutera geo, V3.4 join."""
    return [
        HandlerDef(
            name="sirene_search_company",
            description=(
                "Recherche dans la base SIRENE officielle (toutes entreprises FR, ~30M). "
                "API publique gratuite, sans clé.\n\n"
                "Accepte : nom commercial, raison sociale, dirigeant (nom+prénom), "
                "SIREN (9 chiffres), SIRET (14 chiffres), code NAF.\n\n"
                "Source recommandée pour :\n"
                "- vérifier l'existence d'une entreprise\n"
                "- trouver des concurrents par activité (NAF)\n"
                "- identifier le dirigeant d'une société\n"
                "- récupérer une adresse siège\n\n"
                "**À NE PAS confondre avec datagouv_search** : SIRENE = identité légale "
                "des entreprises. data.gouv = jeux de données ouverts."
            ),
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Nom, SIREN, SIRET, dirigeant, ou code NAF",
                    },
                    "page": {
                        "type": "integer",
                        "description": "Page de résultats (défaut 1)",
                    },
                    "per_page": {
                        "type": "integer",
                        "description": "Résultats par page (max 25, défaut 10)",
                    },
                },
                "required": ["query"],
            },
            handler=sirene_search_company_handler,
            category="web",
            source_module="handlers.sirene",
        ),
        HandlerDef(
            name="sirene_get_by_siret",
            description=(
                "Lookup direct d'une entreprise par son SIRET (14 chiffres). "
                "Retourne identité légale : raison sociale, SIREN, NAF, adresse, "
                "date création, effectif, état administratif, dirigeants.\n\n"
                "Tolère les espaces et tirets dans le SIRET (nettoyés auto). "
                "Si SIRET malformé (≠ 14 chiffres), retourne une erreur claire."
            ),
            parameters={
                "properties": {
                    "siret": {
                        "type": "string",
                        "description": "SIRET (14 chiffres, espaces/tirets tolérés)",
                    },
                },
                "required": ["siret"],
            },
            handler=sirene_get_by_siret_handler,
            category="web",
            source_module="handlers.sirene",
        ),
    ]
