"""
geo_gouv.py — Handlers V2 géocodage France (V3.3).

3 handlers :
  - geo_search_address : adresse → coordonnées GPS (BAN)
  - geo_reverse        : coordonnées → adresse (BAN)
  - geo_commune_info   : code INSEE → métadonnées commune (geo.api.gouv.fr)

Catégorie : `web`. API publiques sans clé. Module distinct de datagouv/SIRENE.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


def _get_service():
    from src.services.geo_gouv import get_geo_gouv_service
    return get_geo_gouv_service()


# ─── geo_search_address ─────────────────────────────────────────────────


async def geo_search_address_handler(
    ctx: HandlerContext,
    query: str,
    limit: int = 5,
    postcode: Optional[str] = None,
) -> HandlerResult:
    try:
        svc = _get_service()
        result = await svc.search_address(query, limit=limit, postcode=postcode)
        features = result.get("features") or []
        if not features:
            return HandlerResult.ok(
                f"Aucune adresse trouvée pour `{query}`.\n\n"
                "💡 Conseils :\n"
                "- Vérifier l'orthographe (numéro + rue + commune)\n"
                "- Restreindre avec `postcode` si l'adresse est commune",
                handler_name="geo_search_address",
            )
        lines = [f"📍 {len(features)} adresse(s) trouvée(s) pour `{query}` :"]
        for i, f in enumerate(features, 1):
            props = f.get("properties") or {}
            coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
            lon, lat = coords[0], coords[1]
            label = props.get("label", "?")
            score = props.get("score")
            score_str = f" (score {score:.2f})" if isinstance(score, (int, float)) else ""
            lines.append(f"\n{i}. **{label}**{score_str}")
            lines.append(f"   coordonnées : lon={lon}, lat={lat}")
            postcode_p = props.get("postcode")
            city = props.get("city")
            citycode = props.get("citycode")
            if postcode_p or city:
                lines.append(f"   {postcode_p or ''} {city or ''} (INSEE: {citycode or '?'})".strip())
        return HandlerResult.ok("\n".join(lines), handler_name="geo_search_address")
    except Exception as e:
        logger.error(f"geo_search_address failed: {e}")
        return HandlerResult.fail(
            f"Erreur recherche adresse : {e}",
            handler_name="geo_search_address",
        )


# ─── geo_reverse ────────────────────────────────────────────────────────


async def geo_reverse_handler(
    ctx: HandlerContext,
    lon: float,
    lat: float,
) -> HandlerResult:
    try:
        from src.services.geo_gouv import GeoError
        svc = _get_service()
        try:
            result = await svc.reverse_geocode(lon, lat)
        except GeoError as ge:
            return HandlerResult.fail(
                f"❌ {ge}",
                handler_name="geo_reverse",
            )
        features = result.get("features") or []
        if not features:
            return HandlerResult.ok(
                f"Aucune adresse à proximité de lon={lon}, lat={lat}.",
                handler_name="geo_reverse",
            )
        f = features[0]
        props = f.get("properties") or {}
        label = props.get("label", "?")
        postcode = props.get("postcode", "")
        city = props.get("city", "")
        citycode = props.get("citycode", "")
        distance = props.get("distance")
        dist_str = f" (à {distance:.0f}m)" if isinstance(distance, (int, float)) else ""
        lines = [
            f"📍 Adresse la plus proche{dist_str} :",
            f"**{label}**",
            f"{postcode} {city} (INSEE: {citycode})".strip(),
        ]
        return HandlerResult.ok("\n".join(lines), handler_name="geo_reverse")
    except Exception as e:
        logger.error(f"geo_reverse failed: {e}")
        return HandlerResult.fail(
            f"Erreur reverse geocoding : {e}",
            handler_name="geo_reverse",
        )


# ─── geo_commune_info ───────────────────────────────────────────────────


async def geo_commune_info_handler(
    ctx: HandlerContext,
    code_insee: str,
) -> HandlerResult:
    try:
        from src.services.geo_gouv import GeoError
        svc = _get_service()
        try:
            commune = await svc.get_commune_info(code_insee)
        except GeoError as ge:
            return HandlerResult.fail(
                f"❌ {ge}",
                handler_name="geo_commune_info",
            )
        if not commune:
            return HandlerResult.ok(
                f"Aucune commune trouvée pour code INSEE `{code_insee}`.",
                handler_name="geo_commune_info",
            )
        nom = commune.get("nom", "?")
        code = commune.get("code", "?")
        codes_postaux = commune.get("codesPostaux") or []
        siren = commune.get("siren", "?")
        epci = commune.get("codeEpci", "?")
        dept = commune.get("codeDepartement", "?")
        region = commune.get("codeRegion", "?")
        pop = commune.get("population")
        surface = commune.get("surface")  # en hectares
        lines = [
            f"🏛️ **{nom}** (INSEE: `{code}`)",
            f"   Codes postaux : {', '.join(codes_postaux) if codes_postaux else '?'}",
            f"   Département : {dept} | Région : {region}",
            f"   SIREN commune : {siren} | EPCI : {epci}",
        ]
        if pop is not None:
            lines.append(f"   Population : {pop}")
        if surface is not None:
            try:
                surface_km2 = float(surface) / 100.0
                lines.append(f"   Surface : {surface_km2:.2f} km²")
            except (TypeError, ValueError):
                pass
        return HandlerResult.ok("\n".join(lines), handler_name="geo_commune_info")
    except Exception as e:
        logger.error(f"geo_commune_info failed: {e}")
        return HandlerResult.fail(
            f"Erreur lookup commune : {e}",
            handler_name="geo_commune_info",
        )


# ─── HandlerDef export ───────────────────────────────────────────────────


def get_geo_gouv_handler_defs() -> list[HandlerDef]:
    """V3.3 : 3 handlers géo. V3.4 ajoutera data_join (croisement multi-fichiers)."""
    return [
        HandlerDef(
            name="geo_search_address",
            description=(
                "Géocodage France via la BAN (Base Adresse Nationale) — api-adresse.data.gouv.fr. "
                "Transforme une adresse libre (numéro + rue + commune) en coordonnées GPS "
                "(longitude, latitude) avec score de confiance.\n\n"
                "API publique, sans clé. Couvre ~25M d'adresses françaises.\n\n"
                "**À ne pas confondre** avec `sirene_*` (entreprises) ou `datagouv_*` (datasets)."
            ),
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Adresse libre (ex: '8 rue de Rivoli Paris')",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de candidats (défaut 5, max 20)",
                    },
                    "postcode": {
                        "type": "string",
                        "description": "Code postal pour restreindre (optionnel)",
                    },
                },
                "required": ["query"],
            },
            handler=geo_search_address_handler,
            category="data",
            source_module="handlers.geo_gouv",
        ),
        HandlerDef(
            name="geo_reverse",
            description=(
                "Reverse geocoding France via la BAN. À partir de coordonnées "
                "(longitude, latitude), retourne l'adresse la plus proche + sa distance.\n\n"
                "Validation : longitude ∈ [-180, 180], latitude ∈ [-90, 90]."
            ),
            parameters={
                "properties": {
                    "lon": {
                        "type": "number",
                        "description": "Longitude (degrés décimaux, ex: 2.3522 pour Paris)",
                    },
                    "lat": {
                        "type": "number",
                        "description": "Latitude (degrés décimaux, ex: 48.8566 pour Paris)",
                    },
                },
                "required": ["lon", "lat"],
            },
            handler=geo_reverse_handler,
            category="data",
            source_module="handlers.geo_gouv",
        ),
        HandlerDef(
            name="geo_commune_info",
            description=(
                "Métadonnées d'une commune française par code INSEE (5 caractères, "
                "ex: 75056 pour Paris, 2A004 pour Ajaccio). Retourne : nom, codes postaux, "
                "département, région, SIREN commune, code EPCI, population, surface.\n\n"
                "Source : geo.api.gouv.fr (officiel, sans clé). "
                "Idéal après `data_unique_values` sur une colonne `code_insee` "
                "pour enrichir les communes."
            ),
            parameters={
                "properties": {
                    "code_insee": {
                        "type": "string",
                        "description": "Code INSEE communal (5 caractères)",
                    },
                },
                "required": ["code_insee"],
            },
            handler=geo_commune_info_handler,
            category="data",
            source_module="handlers.geo_gouv",
        ),
    ]
