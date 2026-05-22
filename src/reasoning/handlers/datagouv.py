"""
datagouv.py — Handlers V2 data.gouv.fr (open data français).

V1 : 3 handlers lecture seule.
API publique, aucune clé requise.

Handlers (3) :
  - datagouv_search           : recherche datasets
  - datagouv_get_dataset      : détails + resources d'un dataset
  - datagouv_download_resource: télécharge dans <workspace>/downloads/datagouv/

V2 ajoute le DataGouv Workbench via handlers/data_workbench.py (profile, query,
aggregate, export). L'ingestion Perception est repoussée hors V2.1.
V3 ajoutera des handlers SIRENE (module séparé) + Géo + scoring + join.

V2.1 : `datagouv_download_resource` écrit un sidecar `<file>.datagouv.json`
contenant provenance (url, format, taille, md5, date) lisible par
`data_profile_file`.

Catégorie : "web" pour les 3 handlers — HTTP public sans credentials,
autonomy_allowed=True. download_resource écrit dans le workspace mais
l'effet primaire reste la requête HTTP (cf tool_categories.py).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


def _compute_md5(path: Path, chunk_size: int = 64 * 1024) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_provenance_sidecar(
    target: Path,
    *,
    resource_url: str,
    format_declared: Optional[str],
    format_detected: str,
    size_bytes: int,
) -> Path:
    """Écrit `<file>.datagouv.json` à côté du fichier téléchargé."""
    sidecar = target.with_suffix(target.suffix + ".datagouv.json")
    # resource_id : extraire depuis URL /api/1/datasets/r/<uuid> si possible
    resource_id = None
    if "/datasets/r/" in resource_url:
        try:
            resource_id = resource_url.split("/datasets/r/")[1].split("/")[0].split("?")[0]
        except Exception:
            resource_id = None
    provenance = {
        "schema_version": 1,
        "resource_url": resource_url,
        "resource_id": resource_id,
        "filename": target.name,
        "format_declared": format_declared,
        "format_detected": format_detected,
        "size_bytes": size_bytes,
        "md5": _compute_md5(target),
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
    }
    sidecar.write_text(
        json.dumps(provenance, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return sidecar


# ── helper ──────────────────────────────────────────────────────────────


def _get_service():
    from src.services.datagouv import get_datagouv_service
    return get_datagouv_service()


# ── helpers signal qualité V1.6 ─────────────────────────────────────────


_MASSIVE_RESOURCES_THRESHOLD = 50  # au-delà, dataset marqué ⚠️

# V3.1 : seuils de scoring
_SCORE_GOOD = 70
_SCORE_ACCEPTABLE = 40

# V3.1.1 : seuils de taille des ressources (bytes)
_SIZE_TINY = 1024              # < 1 KB → ressource quasi vide
_SIZE_SMALL = 10 * 1024        # < 10 KB → suspect
_SIZE_HEALTHY_MAX = 20 * 1024 * 1024  # 20 MB → seuil supérieur "raisonnable"

# V3.1.1 : marqueurs de datasets pauvres (fiche profil, métadonnées seules)
_POOR_DATASET_MARKERS: tuple = (
    "profil acheteur", "profil-acheteur", "profil_acheteur",
    "urlprofilacheteur", "url profil acheteur",
    "fiche acheteur", "fiche profil",
    "dcat-ap", "dcat ap",  # ne pas matcher "dcat" seul (trop large)
    "métadonnées seulement", "metadonnees seulement",
    "coordonnées de l'acheteur", "coordonnees de l'acheteur",
)


def _format_signature(ds: dict) -> list[str]:
    """Liste triée et dédupliquée des formats des resources (lowercase)."""
    fmts = set()
    for r in ds.get("resources") or []:
        f = (r.get("format") or "").lower().strip()
        if f:
            fmts.add(f)
    return sorted(fmts)


def _parse_iso_date(s: Optional[str]):
    """Parse une date ISO 'YYYY-MM-DD...' tolérante. Retourne datetime ou None."""
    if not s or not isinstance(s, str):
        return None
    from datetime import datetime
    try:
        # Accepte "2024-01-15", "2024-01-15T10:00:00", "2024-01-15T10:00:00+00:00"
        return datetime.fromisoformat(s.replace("Z", "+00:00").split("T")[0])
    except (ValueError, TypeError):
        return None


def _best_resource_size_bytes(ds: dict, required_format: Optional[str]) -> Optional[int]:
    """Taille max parmi les ressources (filtrée par format requis si fourni).

    Retourne None si aucune ressource n'a de filesize lisible (incertitude).
    """
    resources = ds.get("resources") or []
    if required_format:
        rf = required_format.lower().strip().lstrip(".")
        resources = [
            r for r in resources
            if (r.get("format") or "").lower() == rf
        ] or resources
    sizes = []
    for r in resources:
        s = r.get("filesize")
        if isinstance(s, (int, float)) and s > 0:
            sizes.append(int(s))
    return max(sizes) if sizes else None


def _matches_poor_marker(text: str) -> bool:
    t = (text or "").lower()
    return any(m in t for m in _POOR_DATASET_MARKERS)


def _dataset_age_days(ds: dict) -> Optional[int]:
    """Nb de jours depuis dernière MAJ. None si inconnu."""
    from datetime import datetime, timezone
    for key in ("last_modified", "last_update", "updated_at", "modified"):
        dt = _parse_iso_date(ds.get(key))
        if dt is not None:
            now = datetime.now()
            return max(0, (now - dt).days)
    return None


def _score_dataset_v3(ds: dict, required_format: Optional[str]) -> tuple:
    """V3.1 + V3.1.1 : score 0-100 + raisons + verdict.

    Architecture du score :
        base 50 + bonuses_capped (max 50) − penalties

    Les bonuses sont plafonnés à 50 (base 50 + bonus_max 50 = 100 max),
    puis les penalties sont retranchées. Cela permet aux pénalités de
    "biter" même quand les bonuses saturent — un dataset 172 octets +
    profil acheteur ne peut pas se cacher derrière format+orga+fraîcheur.

    Plancher 0, plafond 100. Cap supplémentaire à 90 si taille resource inconnue.

    Retourne (score, reasons: list[str], verdict: str).
    """
    bonuses = 0
    penalties = 0
    reasons: list[str] = []

    fmts = _format_signature(ds)
    nb_res = len(ds.get("resources") or [])
    org_name = (ds.get("organization") or {}).get("name") or ""
    title = (ds.get("title") or "").strip()
    desc = (ds.get("description") or "").strip()
    age_days = _dataset_age_days(ds)

    rf = (required_format or "").lower().strip().lstrip(".") or None

    # Format demandé
    if rf:
        if rf in fmts:
            bonuses += 35
            reasons.append(f"format `{rf}` présent (+35)")
        else:
            penalties += 25
            reasons.append(f"pas de `{rf}` (-25)")

    # Massif / vide
    if nb_res == 0:
        penalties += 40
        reasons.append("aucune ressource (-40)")
    elif nb_res > _MASSIVE_RESOURCES_THRESHOLD:
        penalties += 25
        reasons.append(f"massif {nb_res} ressources (-25)")
    else:
        bonuses += 15
        reasons.append(f"{nb_res} ressources (+15)")
        if 1 <= nb_res <= 20:
            bonuses += 10
            reasons.append("taille raisonnable (+10)")

    # Organisme
    if org_name and org_name != "?":
        bonuses += 10
        reasons.append("organisme renseigné (+10)")
    else:
        reasons.append("organisme manquant")

    # Description
    if len(desc) >= 80:
        bonuses += 5
        reasons.append("description renseignée (+5)")

    # Fraîcheur
    if age_days is not None:
        if age_days < 365:
            bonuses += 10
            reasons.append(f"MAJ il y a {age_days}j (+10)")
        elif age_days < 365 * 3:
            bonuses += 5
            reasons.append(f"MAJ il y a {age_days // 30} mois (+5)")
        elif age_days > 365 * 5:
            penalties += 5
            reasons.append(f"MAJ il y a {age_days // 365} ans (-5)")

    # V3.1.1 : taille des ressources
    size_bytes = _best_resource_size_bytes(ds, rf)
    size_unknown = size_bytes is None
    if size_bytes is not None:
        kb = size_bytes / 1024
        if size_bytes < _SIZE_TINY:
            penalties += 35
            reasons.append(f"ressource minuscule {size_bytes}o (-35)")
        elif size_bytes < _SIZE_SMALL:
            penalties += 15
            reasons.append(f"ressource petite {kb:.1f} KB (-15)")
        elif size_bytes <= _SIZE_HEALTHY_MAX:
            bonuses += 10
            if kb < 1024:
                reasons.append(f"taille saine {kb:.0f} KB (+10)")
            else:
                reasons.append(f"taille saine {kb/1024:.1f} MB (+10)")
        else:
            reasons.append(f"taille {kb/1024:.0f} MB (volumineux)")

    # V3.1.1 : marqueurs de dataset pauvre (profil acheteur, DCAT-AP, métadonnées)
    if _matches_poor_marker(title) or _matches_poor_marker(desc):
        penalties += 25
        reasons.append("contenu type fiche/profil acheteur (-25)")

    # Architecture : bonuses cap à 50, puis pénalités retranchées
    bonuses_capped = min(bonuses, 50)
    score = 50 + bonuses_capped - penalties
    score = max(0, min(100, score))

    # V3.1.1 : pas de 100/100 sans signal de richesse confirmé
    if size_unknown and score >= 95:
        score = 90
        reasons.append("plafond 90 — taille des ressources inconnue")

    if score >= _SCORE_GOOD:
        verdict = "✅ choisir ce dataset"
    elif score >= _SCORE_ACCEPTABLE:
        verdict = "⚙️ acceptable"
    else:
        verdict = "⛔ à éviter"

    return score, reasons, verdict


def _score_dataset_for_format(ds: dict, required_format: Optional[str]) -> tuple:
    """Clé de tri legacy (compat) : (priorité has_format, empty, massive, nb_res).

    Le scoring V3.1 est exposé via `_score_dataset_v3` ; cette fonction garde
    le tri ascendant des résultats pour rétro-compat des tests V1.6.
    """
    fmts = _format_signature(ds)
    nb_res = len(ds.get("resources") or [])
    if required_format:
        rf = required_format.lower().strip().lstrip(".")
        has_fmt = 0 if rf in fmts else 1
    else:
        has_fmt = 0
    empty = 1 if nb_res == 0 else 0
    massive = 1 if nb_res > _MASSIVE_RESOURCES_THRESHOLD else 0
    return (has_fmt, empty, massive, nb_res)


# ── handler 1 : search ──────────────────────────────────────────────────


async def datagouv_search_handler(
    ctx: HandlerContext,
    query: str,
    page_size: int = 10,
    organization: Optional[str] = None,
    tag: Optional[str] = None,
    required_format: Optional[str] = None,
) -> HandlerResult:
    try:
        svc = _get_service()
        result = await svc.search_datasets(
            query,
            page_size=page_size,
            organization=organization,
            tag=tag,
        )
        items = result.get("data", []) or []
        total = result.get("total", 0)
        if not items:
            return HandlerResult.ok(
                f"Aucun dataset trouvé pour '{query}'.\n\n"
                f"💡 Conseils :\n"
                f"- Essayer une requête plus courte (1-2 mots-clés : ex. 'population', 'INSEE', 'communes')\n"
                f"- Tester sans accents ni mots de liaison\n"
                f"- Si la donnée existe sur data.gouv mais reste introuvable, "
                f"passer par `web_search` puis revenir avec le slug exact.",
                handler_name="datagouv_search",
            )

        # V3.1 : score multi-critères + tri principal sur score (desc)
        rf_norm = (required_format or "").lower().strip().lstrip(".") or None
        scored = [(ds, *_score_dataset_v3(ds, rf_norm)) for ds in items]
        scored.sort(key=lambda t: -t[1])
        items = [t[0] for t in scored]
        scores_by_slug = {t[0].get("slug", ""): (t[1], t[2], t[3]) for t in scored}

        header = f"📊 {total} datasets trouvés (top {len(items)})"
        if rf_norm:
            header += f" — scoring V3.1 (format requis `{rf_norm}`)"
        else:
            header += " — scoring V3.1"
        lines = [header + " :"]

        # Bilan synthétique en tête
        good = sum(1 for t in scored if t[1] >= _SCORE_GOOD)
        accept = sum(1 for t in scored if _SCORE_ACCEPTABLE <= t[1] < _SCORE_GOOD)
        avoid = sum(1 for t in scored if t[1] < _SCORE_ACCEPTABLE)
        matching_fmt = sum(
            1 for ds in items
            if rf_norm and rf_norm in _format_signature(ds)
        )
        empty_count = sum(1 for ds in items if not (ds.get("resources") or []))
        massive_count = sum(
            1 for ds in items
            if len(ds.get("resources") or []) > _MASSIVE_RESOURCES_THRESHOLD
        )
        lines.append(
            f"   → distribution : ✅ {good} à choisir | ⚙️ {accept} acceptables | ⛔ {avoid} à éviter"
        )
        if rf_norm:
            lines.append(
                f"   → {matching_fmt}/{len(items)} ont une ressource `{rf_norm}` visible"
            )
        if empty_count:
            lines.append(f"   → {empty_count} dataset(s) sans aucune ressource")
        if massive_count:
            lines.append(
                f"   → {massive_count} dataset(s) massif(s) >{_MASSIVE_RESOURCES_THRESHOLD} resources"
            )

        for i, ds in enumerate(items, 1):
            slug = ds.get("slug", "")
            title = ds.get("title", "Sans titre")
            org = (ds.get("organization") or {}).get("name", "?")
            nb_res = len(ds.get("resources") or [])
            fmts = _format_signature(ds)
            fmts_disp = ", ".join(fmts) if fmts else "—"
            score, reasons, verdict = scores_by_slug.get(slug, (0, [], "⛔"))

            # Marqueurs de qualité (gardés pour rétro-compat lecture humaine)
            markers: list[str] = []
            if rf_norm and rf_norm in fmts:
                markers.append(f"✅ {rf_norm.upper()}")
            elif rf_norm:
                markers.append(f"❌ pas de {rf_norm.upper()}")
            if nb_res == 0:
                markers.append("❌ AUCUNE RESSOURCE")
            elif nb_res > _MASSIVE_RESOURCES_THRESHOLD:
                markers.append(f"⚠️ MASSIF ({nb_res} ressources)")
            marker_str = " ".join(markers)

            lines.append(
                f"\n{i}. **{title}** — score {score}/100 {verdict} {marker_str}".rstrip()
            )
            lines.append(f"   slug: `{slug}`")
            lines.append(f"   organisme: {org}")
            lines.append(f"   resources: {nb_res} — formats: [{fmts_disp}]")
            if reasons:
                # 6 raisons pour montrer la taille même si tous les autres bonus sont là
                lines.append(f"   raisons : {' · '.join(reasons[:6])}")
            desc = (ds.get("description") or "").strip()
            if desc:
                lines.append(f"   {desc[:200].replace(chr(10), ' ')}...")

        # Conseils stratégiques
        if rf_norm and matching_fmt == 0:
            lines.append(
                f"\n💡 Aucun résultat n'a de ressource `{rf_norm}` visible. "
                "Essayer une autre requête, augmenter `page_size`, ou accepter un autre format."
            )
        elif good == 0:
            lines.append(
                "\n💡 Aucun dataset au-dessus du seuil de qualité. "
                "Restreindre la requête (ajouter un mot-clé ciblé : zone, année, sujet)."
            )

        return HandlerResult.ok("\n".join(lines), handler_name="datagouv_search")
    except Exception as e:
        logger.error(f"datagouv_search failed: {e}")
        return HandlerResult.fail(
            f"Erreur recherche data.gouv : {e}",
            handler_name="datagouv_search",
        )


# ── handler 2 : get_dataset ─────────────────────────────────────────────


async def datagouv_get_dataset_handler(
    ctx: HandlerContext,
    slug_or_id: str,
    preferred_format: Optional[str] = None,
) -> HandlerResult:
    try:
        svc = _get_service()
        ds = await svc.get_dataset(slug_or_id)
        title = ds.get("title", "Sans titre")
        org = (ds.get("organization") or {}).get("name", "?")
        desc = (ds.get("description") or "").strip()
        freq = ds.get("frequency", "?")
        resources = ds.get("resources") or []

        # V1.6 : tri par format préféré + flag massif
        pf_norm = (preferred_format or "").lower().strip().lstrip(".") or None
        if pf_norm and resources:
            resources = sorted(
                resources,
                key=lambda r: (
                    0 if (r.get("format") or "").lower() == pf_norm else 1,
                    -(r.get("filesize") or 0),  # plus gros = meilleur en cas d'égalité
                ),
            )

        fmts_summary = sorted({(r.get("format") or "").lower() for r in resources if r.get("format")})
        is_massive = len(resources) > _MASSIVE_RESOURCES_THRESHOLD

        lines = [
            f"📊 **{title}**",
            f"organisme : {org}",
            f"fréquence MAJ : {freq}",
            f"resources : {len(resources)} — formats: [{', '.join(fmts_summary) or '—'}]",
        ]
        if is_massive:
            lines.append(
                f"⚠️ DATASET MASSIF (>{_MASSIVE_RESOURCES_THRESHOLD} ressources) — "
                f"liste ci-dessous tronquée à 10 entrées. Si le format demandé "
                "n'apparaît pas, ne pas insister : choisir un autre dataset depuis "
                "`datagouv_search` plutôt que browser/web_fetch."
            )
        if pf_norm:
            n_match = sum(1 for r in resources if (r.get("format") or "").lower() == pf_norm)
            if n_match == 0:
                lines.append(
                    f"❌ Aucune ressource au format `{pf_norm}` dans les 10 premières. "
                    f"Formats disponibles : [{', '.join(fmts_summary) or '—'}]. "
                    "→ Choisir un autre dataset OU accepter un autre format."
                )
            else:
                lines.append(f"✅ {n_match} ressource(s) au format `{pf_norm}` détectée(s) en tête.")
        if desc:
            lines.append(f"\n{desc[:500]}...")
        if resources:
            lines.append(
                "\n**Resources téléchargeables :** "
                "(préférer `latest` à `url` pour stabilité long-terme)"
            )
            for i, r in enumerate(resources[:10], 1):
                rid = r.get("id", "?")
                fmt = r.get("format", "?")
                title_r = r.get("title") or "Sans titre"
                url = r.get("url", "")
                latest = r.get("latest", "")
                size = r.get("filesize")
                size_str = f" ({size / 1024:.0f} KB)" if size else ""
                checksum = r.get("checksum") or {}
                chk = ""
                if isinstance(checksum, dict) and checksum.get("value"):
                    chk = f" sha256/md5: {checksum.get('type', '?')}:{checksum['value'][:16]}…"
                lines.append(f"\n{i}. [{fmt}] {title_r}{size_str}")
                lines.append(f"   id: `{rid}`{chk}")
                if latest:
                    lines.append(f"   latest (stable): `{latest}`")
                if url and url != latest:
                    lines.append(f"   url (direct): `{url}`")
            lines.append(
                "\n💡 Pour télécharger : appeler `datagouv_download_resource` avec :"
                "\n   - `resource_url` = URL `latest` si présente (sinon `url`)"
                "\n   - `filename` = nom propre construit depuis titre+format (ex: `donnees_2024.xlsx`)"
                "\n   - `expected_format` = format demandé par l'utilisateur (csv, json, xlsx...)"
            )
        return HandlerResult.ok(
            "\n".join(lines), handler_name="datagouv_get_dataset"
        )
    except Exception as e:
        logger.error(f"datagouv_get_dataset failed: {e}")
        return HandlerResult.fail(
            f"Erreur récupération dataset : {e}",
            handler_name="datagouv_get_dataset",
        )


# ── handler 3 : download_resource ───────────────────────────────────────


_FORMAT_ALIASES = {
    "xls": {"xls", "xlsx"},
    "xlsx": {"xls", "xlsx"},
    "csv": {"csv"},
    "json": {"json"},
    "geojson": {"geojson", "json"},
    "zip": {"zip"},
    "pdf": {"pdf"},
}


def _detect_format(filename: str) -> str:
    """Extension de fichier sans le point, lowercase."""
    suffix = Path(filename).suffix.lower().lstrip(".")
    return suffix or "bin"


def _format_matches(actual: str, expected: str) -> bool:
    actual = actual.lower()
    expected = expected.lower().lstrip(".")
    allowed = _FORMAT_ALIASES.get(expected, {expected})
    return actual in allowed


async def datagouv_download_resource_handler(
    ctx: HandlerContext,
    resource_url: str,
    filename: Optional[str] = None,
    expected_format: Optional[str] = None,
) -> HandlerResult:
    """
    Télécharge une resource data.gouv dans `<runtime_root>/downloads/datagouv/`.

    Retourne : chemin relatif workspace + chemin absolu + format détecté + taille.
    Si `expected_format` est fourni et ne correspond pas → warning explicite
    sans tenter de conversion (non incluse en V1).
    """
    try:
        svc = _get_service()
        target_dir = Path(ctx.runtime_root) / "downloads" / "datagouv"
        if not filename:
            filename = resource_url.rstrip("/").split("/")[-1] or "dataset.bin"
        filename = Path(filename).name  # bloque path traversal `/` et `\\`
        target = target_dir / filename

        await svc.download_resource(resource_url, target)
        abs_path = target.resolve()
        size_bytes = target.stat().st_size
        size_kb = size_bytes / 1024
        try:
            rel = target.relative_to(ctx.runtime_root)
        except ValueError:
            rel = target
        detected_format = _detect_format(target.name)

        # Écriture sidecar provenance V2.1 (non bloquant si échec)
        sidecar_rel: Optional[str] = None
        try:
            sidecar = _write_provenance_sidecar(
                target,
                resource_url=resource_url,
                format_declared=expected_format,
                format_detected=detected_format,
                size_bytes=size_bytes,
            )
            try:
                sidecar_rel = str(sidecar.relative_to(ctx.runtime_root))
            except ValueError:
                sidecar_rel = str(sidecar)
        except Exception as sce:
            logger.warning(f"sidecar provenance non écrit : {sce}")

        lines = [
            f"✅ Téléchargé : `{rel}` ({size_kb:.1f} KB)",
            f"   chemin absolu : `{abs_path}`",
            f"   format détecté : `{detected_format}`",
        ]
        if sidecar_rel:
            lines.append(f"   provenance : `{sidecar_rel}` (lisible par `data_profile_file`)")

        # Vérification format demandé vs format réel
        format_mismatch = False
        if expected_format:
            if not _format_matches(detected_format, expected_format):
                format_mismatch = True
                lines.append(
                    f"\n⚠️ FORMAT NON CONFORME : tu as demandé `{expected_format}` "
                    f"mais cette resource est `{detected_format}`. "
                    f"La conversion {detected_format.upper()}→{expected_format.upper()} "
                    f"n'est pas incluse en V1 de l'intégration data.gouv. "
                    f"Pour obtenir du {expected_format.upper()}, chercher un autre "
                    f"dataset/resource via `datagouv_get_dataset` "
                    f"ou prévenir l'utilisateur que le format demandé n'est pas disponible."
                )

        if not format_mismatch:
            lines.append(
                "\nProchaine étape suggérée : `data_profile_file` (V2.1) pour comprendre "
                "la structure du fichier (colonnes, dtypes, exemples) avant d'analyser."
            )

        return HandlerResult.ok(
            "\n".join(lines), handler_name="datagouv_download_resource"
        )
    except Exception as e:
        logger.error(f"datagouv_download_resource failed: {e}")
        return HandlerResult.fail(
            f"Erreur téléchargement : {e}",
            handler_name="datagouv_download_resource",
        )


# ── HandlerDef export ────────────────────────────────────────────────────


def get_datagouv_handler_defs() -> list[HandlerDef]:
    """V1 : 3 handlers lecture seule. V2 ajoutera download_and_ingest."""
    return [
        HandlerDef(
            name="datagouv_search",
            description=(
                "Recherche dans 50000+ datasets officiels français de data.gouv.fr "
                "(INSEE, DVF immobilier, marchés publics, démographie, santé). "
                "Source recommandée pour stats officielles France avant web_search.\n\n"
                "**Scoring V3.1** : chaque résultat est noté /100 avec verdict "
                "(✅ choisir / ⚙️ acceptable / ⛔ éviter) et raisons explicites. "
                "Le tri principal est par score décroissant.\n\n"
                "**Règles obligatoires** :\n"
                "1. **Si l'utilisateur demande un format précis (csv/json/xlsx) : TOUJOURS "
                "passer `required_format`.** Sans ça, le scoring ne peut pas pénaliser les "
                "datasets sans le format demandé.\n"
                "2. **Privilégier le dataset n°1 marqué ✅ choisir** (score ≥ 70). S'il échoue, "
                "tester le suivant marqué ✅, puis ⚙️ en dernier recours.\n"
                "3. **Ne PAS choisir un dataset ⛔ à éviter** sauf si tous les autres ont échoué.\n"
                "4. **Si 404 sur une ressource : revenir à `datagouv_search` et tester le dataset "
                "suivant.** Ne PAS basculer en `browser_navigate` / `web_fetch` / `http_request` "
                "pour explorer data.gouv — l'API search/get_dataset suffit.\n"
                "5. L'API préfère les requêtes courtes (1-2 mots-clés)."
            ),
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Mots-clés de recherche",
                    },
                    "page_size": {
                        "type": "integer",
                        "description": "Nombre de résultats (max 100, défaut 10)",
                    },
                    "organization": {
                        "type": "string",
                        "description": "Filtrer par organisme (slug, optionnel)",
                    },
                    "tag": {
                        "type": "string",
                        "description": "Filtrer par tag (optionnel)",
                    },
                    "required_format": {
                        "type": "string",
                        "description": (
                            "Format requis (ex: 'csv', 'json', 'xlsx'). Active le tri qualité "
                            "+ marqueurs ✅/❌/⚠️. À PASSER systématiquement si l'utilisateur "
                            "demande un format précis."
                        ),
                    },
                },
                "required": ["query"],
            },
            handler=datagouv_search_handler,
            category="web",
            source_module="handlers.datagouv",
        ),
        HandlerDef(
            name="datagouv_get_dataset",
            description=(
                "Détails complets d'un dataset data.gouv : métadonnées + liste "
                "des resources téléchargeables (CSV/JSON/XLSX) avec leurs URLs. "
                "À appeler avant datagouv_download_resource.\n\n"
                "**Règles V1.6** :\n"
                "1. Si l'utilisateur veut un format précis : passer `preferred_format` "
                "→ les ressources de ce format remontent en tête, marqueur ✅ visible.\n"
                "2. Si dataset marqué `⚠️ MASSIF` (>50 resources, liste tronquée) : "
                "ne PAS insister via browser/web_fetch. Revenir à `datagouv_search` "
                "et choisir un autre dataset.\n"
                "3. Si `❌ Aucune ressource au format X` : changer de dataset, "
                "ne PAS deviner d'URL."
            ),
            parameters={
                "properties": {
                    "slug_or_id": {
                        "type": "string",
                        "description": "Slug ou ID du dataset (depuis datagouv_search)",
                    },
                    "preferred_format": {
                        "type": "string",
                        "description": (
                            "Format préféré (ex: 'csv'). Les ressources de ce format "
                            "remontent en tête. Marquage ✅/❌ selon disponibilité."
                        ),
                    },
                },
                "required": ["slug_or_id"],
            },
            handler=datagouv_get_dataset_handler,
            category="web",
            source_module="handlers.datagouv",
        ),
        HandlerDef(
            name="datagouv_download_resource",
            description=(
                "Télécharge une resource data.gouv dans `<workspace>/downloads/datagouv/`. "
                "Retourne chemin relatif + chemin absolu + format détecté + taille. "
                "**Préférer l'URL `latest`** (stable, `https://www.data.gouv.fr/api/1/datasets/r/<id>`) "
                "à l'URL directe (peut pointer vers blob Azure/S3 expiré). "
                "Limite 100 MB. "
                "\n\n**Règles obligatoires** :\n"
                "1. **Si l'utilisateur demande un format précis (CSV, JSON, XLSX...), TOUJOURS passer "
                "`expected_format` à chaque appel, y compris sur les retries après 404.**\n"
                "2. **Toujours passer un `filename` propre** construit depuis le titre/format de la resource "
                "(vu dans `datagouv_get_dataset`). Ex: `population_communes_2024.xlsx`. "
                "Si tu n'en donnes pas avec une URL `/datasets/r/<uuid>`, le fichier sera nommé "
                "par son UUID sans extension — illisible.\n"
                "3. NE lance PAS l'ingestion automatique : appeler `ingest_document` ensuite.\n"
                "4. Si 404, essayer une autre resource du même dataset (en conservant `expected_format`)."
            ),
            parameters={
                "properties": {
                    "resource_url": {
                        "type": "string",
                        "description": "URL directe du fichier (préférer `latest` de datagouv_get_dataset)",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Nom de fichier local optionnel (sinon dérivé de l'URL)",
                    },
                    "expected_format": {
                        "type": "string",
                        "description": "Format attendu (ex: 'csv', 'json', 'xlsx'). Le handler avertit si mismatch. Aucune conversion automatique.",
                    },
                },
                "required": ["resource_url"],
            },
            handler=datagouv_download_resource_handler,
            category="web",
            source_module="handlers.datagouv",
        ),
    ]
