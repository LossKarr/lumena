"""
data_workbench.py — Handlers V2 DataGouv Workbench.

V2.1 : un seul handler `data_profile_file` (lecture + profilage).
V2.2 ajoutera data_filter_rows. V2.3 aggregate/unique. V2.4 export.

Catégorie : `documents` (workspace requis, autonomy_allowed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

from src.tools.data_workbench import (
    AggregateResult,
    DEFAULT_FILTER_LIMIT,
    DEFAULT_GROUPS,
    DEFAULT_JOIN_LIMIT,
    DEFAULT_UNIQUE_LIMIT,
    ExportResult,
    FilterError,
    FilterResult,
    JoinResult,
    MAX_EXPORT_ROWS,
    MAX_FILTER_LIMIT,
    MAX_GROUPS,
    MAX_JOIN_OUTPUT_ROWS,
    MAX_ROWS_PROFILE,
    MAX_UNIQUE_LIMIT,
    ProfileResult,
    UniqueValuesResult,
    aggregate_data,
    data_join,
    export_data,
    filter_rows,
    profile_file,
    unique_values,
)
from src.tools.data_workbench import _ALLOWED_OPS, _ALLOWED_EXPORT_FORMATS


_MAX_OUTPUT_CHARS = 8000


def _format_profile(r: ProfileResult) -> str:
    lines = [f"📊 Profil de `{Path(r.path).name}`"]

    if r.provenance:
        p = r.provenance
        if p.get("dataset_slug"):
            lines.append(f"Dataset data.gouv : `{p.get('dataset_slug')}`")
        if p.get("resource_id"):
            lines.append(f"Resource id : `{p.get('resource_id')}`")
        if p.get("resource_url"):
            lines.append(f"URL source : `{p.get('resource_url')}`")
        if p.get("format_declared"):
            lines.append(f"Format annoncé : `{p.get('format_declared')}`")
        if p.get("size_bytes"):
            lines.append(f"Taille : {p.get('size_bytes')} octets")
        if p.get("md5"):
            lines.append(f"Hash MD5 : `{p.get('md5')}`")
        if p.get("downloaded_at"):
            lines.append(f"Téléchargé : {p.get('downloaded_at')}")

    lines.append(
        f"Format détecté : {r.format} | Lignes : {r.rows} | Colonnes : {r.cols}"
    )
    if r.encoding_used:
        lines.append(
            f"Encoding : {r.encoding_used} | Séparateur : `{r.separator_used}`"
        )

    if r.columns:
        lines.append("\n**Colonnes** :")
        for c in r.columns:
            tags = []
            if c.is_territory:
                tags.append("territoire")
            if c.is_date_probable:
                tags.append("date")
            if c.is_numeric_probable:
                tags.append("numeric")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            null_str = (
                f"{c.null_pct}% null ({c.null_count})"
                if c.null_pct
                else "0% null"
            )
            sample = ", ".join(repr(s)[:30] for s in c.sample[:2]) if c.sample else "—"
            lines.append(
                f"- `{c.name}` ({c.dtype}){tag_str} — {null_str} — ex: {sample}"
            )

    if r.sample_rows:
        lines.append("\n**Exemples (5 premières lignes)** :")
        for i, row in enumerate(r.sample_rows[:5], 1):
            items = list(row.items())[:5]
            preview = " | ".join(f"{k}={v}" for k, v in items)
            if len(row) > 5:
                preview += " | ..."
            lines.append(f"{i}. {preview}")

    if r.limits:
        lines.append("\n**Limites détectées** :")
        for lim in r.limits:
            lines.append(f"- {lim}")

    if r.truncated:
        lines.append(
            "\n⚠️ Fichier tronqué. Pour analyser tout : augmenter `max_rows`, "
            "ou utiliser `data_filter_rows` pour cibler un sous-ensemble."
        )

    out = "\n".join(lines)
    if len(out) > _MAX_OUTPUT_CHARS:
        out = out[: _MAX_OUTPUT_CHARS - 80] + "\n\n[…tronqué pour budget tokens…]"
    return out


# ─── handler : profile ──────────────────────────────────────────────────


def _format_filter_result(r: FilterResult, where: list, sort: list, limit: int) -> str:
    from pathlib import Path as _P
    if r.error:
        return f"❌ Filtre impossible : {r.error}"

    lines = [
        f"🔍 Filtre sur `{_P(r.path).name}`",
        f"Lignes scannées : {r.total_scanned} | Matched : {r.total_matched} | "
        f"Retournées : {len(r.rows)}",
    ]
    if where:
        lines.append(f"Filtres : {where}")
    if sort:
        lines.append(f"Tri : {sort}")
    if r.truncated_at_load:
        lines.append("⚠️ Fichier tronqué au chargement (augmenter `max_rows_load`)")
    if r.truncated_at_limit:
        lines.append(
            f"⚠️ Plus de matches que `limit` ({r.total_matched} > {limit}). "
            f"Augmenter `limit` (max {MAX_FILTER_LIMIT}) ou affiner le filtre."
        )

    if not r.columns:
        lines.append("\nFichier vide.")
        return "\n".join(lines)

    if not r.rows:
        lines.append("\nAucune ligne ne correspond au filtre.")
        return "\n".join(lines)

    # Tableau markdown : max 8 colonnes affichées (sinon on liste les autres)
    cols = r.columns
    shown = cols[:8]
    hidden = cols[8:]
    sep = "|"
    header_row = sep + sep.join(f" {h} " for h in shown) + sep
    sep_row = sep + sep.join("---" for _ in shown) + sep
    lines.append("\n" + header_row)
    lines.append(sep_row)
    for row in r.rows:
        cells = [str(row[i]) if i < len(row) else "" for i in range(len(shown))]
        # tronquer chaque cellule à 60 chars
        cells = [c.replace("|", "\\|")[:60] for c in cells]
        lines.append(sep + sep.join(f" {c} " for c in cells) + sep)
    if hidden:
        lines.append(f"\n_({len(hidden)} colonnes masquées : {hidden[:5]}...)_")

    out = "\n".join(lines)
    if len(out) > 8000:
        out = out[: 8000 - 80] + "\n\n[…tronqué pour budget tokens, augmenter le filtre…]"
    return out


# ─── handler : filter_rows ──────────────────────────────────────────────


async def data_filter_rows_handler(
    ctx: HandlerContext,
    path: str,
    where: Optional[list] = None,
    sort: Optional[list] = None,
    limit: int = DEFAULT_FILTER_LIMIT,
    max_rows_load: Optional[int] = None,
) -> HandlerResult:
    try:
        from pathlib import Path
        p = Path(path)
        if not p.is_absolute():
            p = Path(ctx.runtime_root) / p
        # Normalize : where peut venir comme une seule condition (dict) ou liste
        if isinstance(where, dict):
            where = [where]
        elif where is None:
            where = []
        if isinstance(sort, str):
            sort = [sort]
        elif sort is None:
            sort = []
        try:
            r = filter_rows(
                p, where=where, sort=sort, limit=limit, max_rows_load=max_rows_load,
            )
        except FilterError as fe:
            return HandlerResult.fail(
                f"❌ Filtre invalide : {fe}",
                handler_name="data_filter_rows",
            )
        if r.error:
            return HandlerResult.fail(
                f"❌ {r.error}", handler_name="data_filter_rows"
            )
        return HandlerResult.ok(
            _format_filter_result(r, where, sort, limit),
            handler_name="data_filter_rows",
        )
    except Exception as e:
        logger.error(f"data_filter_rows failed: {e}")
        return HandlerResult.fail(
            f"Erreur filtre : {e}", handler_name="data_filter_rows"
        )


def _format_aggregate_result(r: AggregateResult, limit: int) -> str:
    from pathlib import Path as _P
    if r.error:
        return f"❌ Agrégation impossible : {r.error}"

    agg_label = f"{r.agg}({r.agg_col})" if r.agg_col else f"{r.agg}()"
    lines = [
        f"📈 Agrégation `{agg_label}` sur `{_P(r.path).name}`",
        f"Group by : {r.group_by}",
        f"Lignes scannées : {r.total_scanned} | Groupes : {r.total_groups} | "
        f"Retournés : {len(r.rows)}",
    ]
    if r.truncated_at_load:
        lines.append("⚠️ Fichier tronqué au chargement (augmenter `max_rows_load`)")
    if r.truncated_at_limit:
        lines.append(
            f"⚠️ Plus de groupes que `limit` ({r.total_groups} > {limit}). "
            f"Augmenter `limit` (max {MAX_GROUPS}) ou pré-filtrer avec `where`."
        )

    if not r.rows:
        lines.append("\nAucun groupe — vérifier `group_by` et `where`.")
        return "\n".join(lines)

    # Tableau markdown : group_by columns + result + _count
    cols = list(r.group_by) + ["result", "_count"]
    sep = "|"
    lines.append("\n" + sep + sep.join(f" {c} " for c in cols) + sep)
    lines.append(sep + sep.join("---" for _ in cols) + sep)
    for row in r.rows:
        cells = []
        for c in cols:
            v = row.get(c, "")
            if isinstance(v, float):
                cells.append(f"{v:.4g}")
            else:
                cells.append(str(v)[:60].replace("|", "\\|"))
        lines.append(sep + sep.join(f" {c} " for c in cells) + sep)

    out = "\n".join(lines)
    if len(out) > 8000:
        out = out[: 8000 - 80] + "\n\n[…tronqué pour budget tokens…]"
    return out


def _format_unique_values(r: UniqueValuesResult, limit: int) -> str:
    from pathlib import Path as _P
    if r.error:
        return f"❌ Unique values impossible : {r.error}"

    lines = [
        f"📊 Valeurs uniques de `{r.column}` dans `{_P(r.path).name}`",
        f"Lignes scannées : {r.total_scanned} | Valeurs distinctes : {r.total_unique} | "
        f"Retournées : {len(r.values)}",
    ]
    if r.truncated_at_load:
        lines.append("⚠️ Fichier tronqué au chargement")
    if r.truncated_at_limit:
        lines.append(
            f"⚠️ Plus de valeurs uniques que `limit` ({r.total_unique} > {limit}). "
            f"Augmenter `limit` (max {MAX_UNIQUE_LIMIT})."
        )

    if not r.values:
        lines.append("\nAucune valeur (colonne vide ou que des nulls).")
        return "\n".join(lines)

    lines.append("\n| Valeur | Fréquence |")
    lines.append("|---|---|")
    for value, count in r.values:
        v = str(value)[:80].replace("|", "\\|")
        lines.append(f"| {v} | {count} |")

    out = "\n".join(lines)
    if len(out) > 8000:
        out = out[: 8000 - 80] + "\n\n[…tronqué…]"
    return out


# ─── handler : aggregate ────────────────────────────────────────────────


async def data_aggregate_handler(
    ctx: HandlerContext,
    path: str,
    group_by,
    agg: str,
    agg_col: Optional[str] = None,
    where: Optional[list] = None,
    sort: Optional[list] = None,
    limit: int = DEFAULT_GROUPS,
    max_rows_load: Optional[int] = None,
) -> HandlerResult:
    try:
        from pathlib import Path
        p = Path(path)
        if not p.is_absolute():
            p = Path(ctx.runtime_root) / p
        if isinstance(where, dict):
            where = [where]
        if isinstance(sort, str):
            sort = [sort]
        try:
            r = aggregate_data(
                p, group_by=group_by, agg=agg, agg_col=agg_col,
                where=where, sort=sort, limit=limit,
                max_rows_load=max_rows_load,
            )
        except FilterError as fe:
            return HandlerResult.fail(
                f"❌ Agrégation invalide : {fe}",
                handler_name="data_aggregate",
            )
        if r.error:
            return HandlerResult.fail(
                f"❌ {r.error}", handler_name="data_aggregate"
            )
        return HandlerResult.ok(
            _format_aggregate_result(r, limit),
            handler_name="data_aggregate",
        )
    except Exception as e:
        logger.error(f"data_aggregate failed: {e}")
        return HandlerResult.fail(
            f"Erreur agrégation : {e}", handler_name="data_aggregate"
        )


# ─── handler : unique_values ────────────────────────────────────────────


async def data_unique_values_handler(
    ctx: HandlerContext,
    path: str,
    column: str,
    limit: int = DEFAULT_UNIQUE_LIMIT,
    max_rows_load: Optional[int] = None,
    include_empty: bool = False,
) -> HandlerResult:
    try:
        from pathlib import Path
        p = Path(path)
        if not p.is_absolute():
            p = Path(ctx.runtime_root) / p
        try:
            r = unique_values(
                p, column=column, limit=limit,
                max_rows_load=max_rows_load, include_empty=include_empty,
            )
        except FilterError as fe:
            return HandlerResult.fail(
                f"❌ Unique values invalide : {fe}",
                handler_name="data_unique_values",
            )
        if r.error:
            return HandlerResult.fail(
                f"❌ {r.error}", handler_name="data_unique_values"
            )
        return HandlerResult.ok(
            _format_unique_values(r, limit),
            handler_name="data_unique_values",
        )
    except Exception as e:
        logger.error(f"data_unique_values failed: {e}")
        return HandlerResult.fail(
            f"Erreur unique values : {e}", handler_name="data_unique_values"
        )


# ─── handler : join (V3.4) ──────────────────────────────────────────────


def _format_join_result(r: JoinResult, limit: int) -> str:
    from pathlib import Path as _P
    if r.error:
        return f"❌ Jointure impossible : {r.error}"
    lines = [
        f"🔗 Jointure `{r.how}` sur `{r.on_left}` = `{r.on_right}`",
        f"   left  : `{_P(r.left_path).name}` ({r.total_left} lignes)",
        f"   right : `{_P(r.right_path).name}` ({r.total_right} lignes)",
        f"   résultat : {r.total_joined} lignes joined | retournées : {len(r.rows)}",
    ]
    if r.truncated_at_left:
        lines.append("⚠️ Fichier gauche tronqué au chargement (augmenter max_rows_load)")
    if r.truncated_at_right:
        lines.append("⚠️ Fichier droit tronqué au chargement")
    if r.truncated_at_output:
        lines.append(
            f"⚠️ Plus de lignes joined que `limit` ({r.total_joined} > {limit}). "
            f"Affiner les filtres en amont ou augmenter `limit` (max {MAX_JOIN_OUTPUT_ROWS})."
        )

    if not r.columns:
        return "\n".join(lines)
    if not r.rows:
        lines.append("\nAucune correspondance — vérifier `on_left` / `on_right`.")
        return "\n".join(lines)

    # Tableau Markdown : max 8 colonnes affichées
    cols = r.columns
    shown = cols[:8]
    hidden = cols[8:]
    sep = "|"
    lines.append("\n**Aperçu (5 premières lignes) :**")
    lines.append(sep + sep.join(f" {h} " for h in shown) + sep)
    lines.append(sep + sep.join("---" for _ in shown) + sep)
    for row in r.rows[:5]:
        cells = [str(row[i]) if i < len(row) else "" for i in range(len(shown))]
        cells = [c.replace("|", "\\|")[:60] for c in cells]
        lines.append(sep + sep.join(f" {c} " for c in cells) + sep)
    if hidden:
        lines.append(f"_({len(hidden)} colonnes masquées : {hidden[:5]}...)_")

    out = "\n".join(lines)
    if len(out) > 8000:
        out = out[: 8000 - 80] + "\n\n[…tronqué pour budget tokens…]"
    return out


async def data_join_handler(
    ctx: HandlerContext,
    left_path: str,
    right_path: str,
    on_left: str,
    on_right: Optional[str] = None,
    how: str = "inner",
    limit: int = DEFAULT_JOIN_LIMIT,
    max_rows_load: Optional[int] = None,
) -> HandlerResult:
    try:
        from pathlib import Path
        l = Path(left_path)
        r = Path(right_path)
        if not l.is_absolute():
            l = Path(ctx.runtime_root) / l
        if not r.is_absolute():
            r = Path(ctx.runtime_root) / r
        try:
            result = data_join(
                l, r, on_left=on_left, on_right=on_right,
                how=how, limit=limit, max_rows_load=max_rows_load,
            )
        except FilterError as fe:
            return HandlerResult.fail(
                f"❌ Jointure invalide : {fe}",
                handler_name="data_join",
            )
        if result.error:
            return HandlerResult.fail(
                f"❌ {result.error}", handler_name="data_join"
            )
        return HandlerResult.ok(
            _format_join_result(result, limit),
            handler_name="data_join",
        )
    except Exception as e:
        logger.error(f"data_join failed: {e}")
        return HandlerResult.fail(
            f"Erreur jointure : {e}", handler_name="data_join"
        )


# ─── handler : export ───────────────────────────────────────────────────


def _format_export_result(r: ExportResult, ctx_runtime_root) -> str:
    from pathlib import Path as _P
    if r.error:
        return f"❌ Export impossible : {r.error}"
    output = _P(r.output_path)
    sidecar = _P(r.sidecar_path) if r.sidecar_path else None
    try:
        rel_out = output.relative_to(ctx_runtime_root)
    except (ValueError, TypeError):
        rel_out = output
    try:
        rel_side = sidecar.relative_to(ctx_runtime_root) if sidecar else None
    except (ValueError, TypeError):
        rel_side = sidecar
    size_kb = output.stat().st_size / 1024 if output.exists() else 0

    lines = [
        f"✅ Export `{r.output_format}` — {r.rows_exported} lignes",
        f"   fichier : `{rel_out}` ({size_kb:.1f} KB)",
        f"   chemin absolu : `{output.resolve()}`",
    ]
    if rel_side:
        lines.append(f"   sidecar provenance : `{rel_side}`")
    ops = r.operations
    if ops.get("where"):
        lines.append(f"   filtre appliqué : {ops['where']}")
    if ops.get("group_by"):
        lines.append(f"   group_by : {ops['group_by']} | agg : {ops['agg']}({ops.get('agg_col') or ''})")
    if ops.get("columns"):
        lines.append(f"   colonnes projetées : {ops['columns']}")
    if ops.get("sort"):
        lines.append(f"   tri : {ops['sort']}")
    if r.truncated_at_load:
        lines.append("⚠️ Source tronquée au chargement (augmenter `max_rows_load`)")
    if r.truncated_at_export:
        lines.append(
            f"⚠️ Export tronqué (plafond {MAX_EXPORT_ROWS} lignes). "
            "Filtrer davantage en amont si nécessaire."
        )

    # Preview Markdown : évite un read_file après export
    if r.preview_headers and r.preview_rows:
        shown = r.preview_headers[:8]
        hidden = r.preview_headers[8:]
        sep = "|"
        lines.append("\n**Aperçu (5 premières lignes) :**")
        lines.append(sep + sep.join(f" {h} " for h in shown) + sep)
        lines.append(sep + sep.join("---" for _ in shown) + sep)
        for row in r.preview_rows:
            cells = [str(row[i]) if i < len(row) else "" for i in range(len(shown))]
            cells = [c.replace("|", "\\|")[:60] for c in cells]
            lines.append(sep + sep.join(f" {c} " for c in cells) + sep)
        if hidden:
            lines.append(f"_({len(hidden)} colonnes masquées : {hidden[:5]}...)_")

    return "\n".join(lines)


async def data_export_handler(
    ctx: HandlerContext,
    path: str,
    output_format: str,
    where: Optional[list] = None,
    group_by=None,
    agg: Optional[str] = None,
    agg_col: Optional[str] = None,
    sort: Optional[list] = None,
    limit: Optional[int] = None,
    columns: Optional[list] = None,
    filename: Optional[str] = None,
    max_rows_load: Optional[int] = None,
) -> HandlerResult:
    try:
        from pathlib import Path
        src = Path(path)
        if not src.is_absolute():
            src = Path(ctx.runtime_root) / src

        if isinstance(where, dict):
            where = [where]
        if isinstance(sort, str):
            sort = [sort]
        if isinstance(group_by, str):
            group_by = [group_by]
        if isinstance(columns, str):
            columns = [columns]

        # Nom de sortie
        fmt = (output_format or "").lower().lstrip(".")
        if not filename:
            stem = src.stem if src else "export"
            filename = f"{stem}_export.{fmt}"
        # bloque path traversal
        filename = Path(filename).name
        # force la bonne extension
        if not filename.lower().endswith(f".{fmt}"):
            filename = f"{Path(filename).stem}.{fmt}"

        out_dir = Path(ctx.runtime_root) / "exports" / "datagouv"
        output_path = out_dir / filename

        try:
            r = export_data(
                src, output_path,
                output_format=output_format,
                where=where, group_by=group_by, agg=agg, agg_col=agg_col,
                sort=sort, limit=limit, columns=columns,
                max_rows_load=max_rows_load,
            )
        except FilterError as fe:
            return HandlerResult.fail(
                f"❌ Export invalide : {fe}",
                handler_name="data_export",
            )
        if r.error:
            return HandlerResult.fail(
                f"❌ {r.error}", handler_name="data_export"
            )
        return HandlerResult.ok(
            _format_export_result(r, ctx.runtime_root),
            handler_name="data_export",
        )
    except Exception as e:
        logger.error(f"data_export failed: {e}")
        return HandlerResult.fail(
            f"Erreur export : {e}", handler_name="data_export"
        )


async def data_profile_file_handler(
    ctx: HandlerContext,
    path: str,
    max_rows: Optional[int] = None,
) -> HandlerResult:
    try:
        p = Path(path)
        if not p.is_absolute():
            p = Path(ctx.runtime_root) / p
        result = profile_file(p, max_rows=max_rows)
        if result.error:
            return HandlerResult.fail(
                f"❌ Profilage impossible : {result.error}",
                handler_name="data_profile_file",
            )
        return HandlerResult.ok(
            _format_profile(result), handler_name="data_profile_file"
        )
    except Exception as e:
        logger.error(f"data_profile_file failed: {e}")
        return HandlerResult.fail(
            f"Erreur profilage : {e}", handler_name="data_profile_file"
        )


# ─── HandlerDef export ──────────────────────────────────────────────────


def get_data_workbench_handler_defs() -> list[HandlerDef]:
    """V2.1 profile + V2.2 filter + V2.3 aggregate/unique + V2.4 export + V3.4 join."""
    return [
        HandlerDef(
            name="data_join",
            description=(
                "Jointure entre deux fichiers tabulaires existants via une clé commune "
                "(souvent un code INSEE, SIRET, ou identifiant). Travaille sur fichiers du "
                "workspace — n'appelle PAS `datagouv_*` ni `sirene_*`.\n\n"
                "**Types de jointure whitelistés** : `inner`, `left`, `right`, `outer`.\n\n"
                "**Paramètres** :\n"
                "- `left_path` / `right_path` : chemins des deux fichiers (workspace)\n"
                "- `on_left` : nom de la colonne clé dans le fichier gauche\n"
                "- `on_right` (optionnel) : nom dans le fichier droit (défaut = on_left)\n"
                "- `how` : type de jointure (défaut inner)\n"
                "- `limit` : lignes max retournées (défaut 1000, max 100000)\n\n"
                "**Conflits de noms de colonnes** : suffixe `_right` automatique. "
                "La colonne clé n'est pas dupliquée.\n\n"
                "**Usage typique** : enrichir un dataset data.gouv avec un autre "
                "(ex: marchés publics + populations communes via `code_insee`)."
            ),
            parameters={
                "properties": {
                    "left_path": {"type": "string", "description": "Fichier gauche"},
                    "right_path": {"type": "string", "description": "Fichier droit"},
                    "on_left": {"type": "string", "description": "Colonne clé dans le fichier gauche"},
                    "on_right": {
                        "type": "string",
                        "description": "Colonne clé dans le fichier droit (défaut = on_left)",
                    },
                    "how": {
                        "type": "string",
                        "description": "inner | left | right | outer (défaut inner)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Lignes max retournées (défaut {DEFAULT_JOIN_LIMIT}, max {MAX_JOIN_OUTPUT_ROWS})",
                    },
                    "max_rows_load": {
                        "type": "integer",
                        "description": "Lignes max chargées depuis chaque fichier",
                    },
                },
                "required": ["left_path", "right_path", "on_left"],
            },
            handler=data_join_handler,
            category="documents",
            source_module="handlers.data_workbench",
        ),
        HandlerDef(
            name="data_export",
            description=(
                "Exporte un fichier transformé (filtré et/ou agrégé) en CSV/JSON/XLSX "
                "dans `<workspace>/exports/datagouv/`. Travaille sur un fichier existant — "
                "n'appelle PAS `datagouv_*`.\n\n"
                "**Pipeline** :\n"
                "- Si `group_by` fourni : agrégation puis export (résultat = colonnes group_by + result + _count)\n"
                "- Sinon : filter + project (`columns`) + export\n"
                "- `where` optionnel : pré-filtre identique à `data_filter_rows`\n"
                "- `sort` et `limit` appliqués avant export\n"
                "- `columns` (filter mode) : sous-ensemble de colonnes à conserver\n\n"
                "**Formats whitelistés** : `csv`, `json`, `xlsx`.\n\n"
                "Écrit aussi un sidecar `<file>.export_meta.json` avec source + hash MD5 + "
                "opérations + date. Limite dure : 100 000 lignes par export."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier source"},
                    "output_format": {
                        "type": "string",
                        "description": "csv | json | xlsx",
                    },
                    "where": {
                        "type": "array",
                        "description": "Pré-filtre [{col, op, value}, ...] optionnel",
                    },
                    "group_by": {
                        "type": "array",
                        "description": "Colonnes de groupage (agrégation, optionnel)",
                    },
                    "agg": {
                        "type": "string",
                        "description": "count|sum|mean|min|max|median (requis si group_by)",
                    },
                    "agg_col": {
                        "type": "string",
                        "description": "Colonne numérique à agréger (requis sauf count)",
                    },
                    "sort": {
                        "type": "array",
                        "description": "Tri (mêmes specs que data_filter_rows / data_aggregate)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Lignes max exportées (défaut illimité, plafond dur 100000)",
                    },
                    "columns": {
                        "type": "array",
                        "description": "Sous-ensemble de colonnes à exporter (filter mode uniquement)",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Nom du fichier de sortie (extension corrigée auto)",
                    },
                    "max_rows_load": {
                        "type": "integer",
                        "description": "Lignes max chargées depuis la source",
                    },
                },
                "required": ["path", "output_format"],
            },
            handler=data_export_handler,
            category="documents",
            source_module="handlers.data_workbench",
        ),
        HandlerDef(
            name="data_aggregate",
            description=(
                "Group by + agrégation sur un fichier tabulaire déjà téléchargé. "
                "Travaille sur un fichier existant — n'appelle PAS `datagouv_*`. "
                "Idéal pour répondre à des questions de type 'top N par catégorie', "
                "'somme par région', 'moyenne par mois'.\n\n"
                "**Agrégations whitelistées** : `count`, `sum`, `mean` (alias `avg`/`average`), "
                "`min`, `max`, `median`.\n\n"
                "**Paramètres** :\n"
                "- `group_by` : colonne ou liste de colonnes (max 3).\n"
                "- `agg` : nom de l'agrégation.\n"
                "- `agg_col` : colonne numérique à agréger (requis sauf `count`).\n"
                "- `where` (optionnel) : pré-filtre identique à `data_filter_rows`.\n"
                "- `sort` (optionnel) : tri sur `group_by` ou `result`/`_count`. "
                "Ex: `[\"-result\"]` pour top descendant.\n"
                "- `limit` : nb groupes retournés (défaut 100, max 1000)."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier"},
                    "group_by": {
                        "type": "array",
                        "description": "Colonne(s) de groupage (string ou liste, max 3)",
                    },
                    "agg": {
                        "type": "string",
                        "description": "count | sum | mean | min | max | median",
                    },
                    "agg_col": {
                        "type": "string",
                        "description": "Colonne numérique à agréger (requis sauf count)",
                    },
                    "where": {
                        "type": "array",
                        "description": "Pré-filtre [{col, op, value}, ...] (optionnel)",
                    },
                    "sort": {
                        "type": "array",
                        "description": "Tri sur group_by/result/_count (optionnel)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": f"Nb groupes (défaut {DEFAULT_GROUPS}, max {MAX_GROUPS})",
                    },
                    "max_rows_load": {
                        "type": "integer",
                        "description": "Lignes max chargées",
                    },
                },
                "required": ["path", "group_by", "agg"],
            },
            handler=data_aggregate_handler,
            category="documents",
            source_module="handlers.data_workbench",
        ),
        HandlerDef(
            name="data_unique_values",
            description=(
                "Liste les valeurs distinctes d'une colonne avec leur fréquence. "
                "Travaille sur un fichier existant — n'appelle PAS `datagouv_*`. "
                "Idéal pour découvrir les valeurs d'une colonne catégorielle "
                "(régions, codes NAF, statuts...) avant de filtrer/agréger.\n\n"
                "Tri : fréquence décroissante. Valeurs vides exclues par défaut."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier"},
                    "column": {"type": "string", "description": "Colonne à analyser"},
                    "limit": {
                        "type": "integer",
                        "description": (
                            f"Valeurs max retournées "
                            f"(défaut {DEFAULT_UNIQUE_LIMIT}, max {MAX_UNIQUE_LIMIT})"
                        ),
                    },
                    "max_rows_load": {
                        "type": "integer",
                        "description": "Lignes max chargées",
                    },
                    "include_empty": {
                        "type": "boolean",
                        "description": "Inclure les valeurs vides (défaut false)",
                    },
                },
                "required": ["path", "column"],
            },
            handler=data_unique_values_handler,
            category="documents",
            source_module="handlers.data_workbench",
        ),
        HandlerDef(
            name="data_filter_rows",
            description=(
                "Filtre + trie + limite un fichier tabulaire (CSV/XLSX/JSON) déjà téléchargé "
                "et profilé. Travaille sur un fichier existant du workspace — n'appelle PAS "
                "`datagouv_search` ni `datagouv_download_resource`. À appeler après "
                "`data_profile_file` qui te donne le nom des colonnes.\n\n"
                "**Opérateurs whitelistés** : `==`, `!=`, `>`, `<`, `>=`, `<=`, "
                "`contains`, `in`, `not_in`, `startswith`. Tout autre opérateur est refusé.\n\n"
                "**Format `where`** : liste de conditions `{\"col\": \"region\", \"op\": \"==\", "
                "\"value\": \"Île-de-France\"}`. Plusieurs conditions = ET logique.\n\n"
                "**Format `sort`** : liste comme `[\"-population\", \"+commune\"]` "
                "(préfixe `-` = desc, `+` ou rien = asc) ou `[{\"col\": \"population\", "
                "\"order\": \"desc\"}]`.\n\n"
                "**Limit** : défaut 50, max 1000. Si dépassé : augmenter le filtre, "
                "pas le limit. L'export d'un résultat complet arrive en V2.4."
            ),
            parameters={
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin du fichier (relatif workspace ou absolu)",
                    },
                    "where": {
                        "type": "array",
                        "description": (
                            "Liste de conditions [{col, op, value}, ...]. "
                            "Ops : ==, !=, >, <, >=, <=, contains, in, not_in, startswith."
                        ),
                    },
                    "sort": {
                        "type": "array",
                        "description": (
                            "Liste de specs de tri. Strings `-col`/`+col`/`col`, "
                            "ou objets {col, order: asc|desc}."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            f"Lignes max retournées (défaut {DEFAULT_FILTER_LIMIT}, "
                            f"max {MAX_FILTER_LIMIT})"
                        ),
                    },
                    "max_rows_load": {
                        "type": "integer",
                        "description": (
                            "Lignes max chargées depuis le fichier "
                            f"(défaut {MAX_ROWS_PROFILE})"
                        ),
                    },
                },
                "required": ["path"],
            },
            handler=data_filter_rows_handler,
            category="documents",
            source_module="handlers.data_workbench",
        ),
        HandlerDef(
            name="data_profile_file",
            description=(
                "Profile un fichier tabulaire (CSV/XLSX/JSON) téléchargé : "
                "nombre de lignes, colonnes avec dtype inféré, valeurs manquantes, "
                "exemples, colonnes dates/territoire/numériques probables, limites. "
                "Lit aussi la provenance data.gouv (sidecar `.datagouv.json` à côté du fichier) "
                "si présente. À appeler après `datagouv_download_resource` pour comprendre "
                "le contenu avant toute analyse. Filter/aggregate/export arrivent en V2.2-V2.4."
            ),
            parameters={
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin du fichier (relatif workspace ou absolu)",
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": (
                            f"Lignes max à profiler (défaut {MAX_ROWS_PROFILE}, "
                            "configurable via env LUMENA_DATA_WB_MAX_ROWS)"
                        ),
                    },
                },
                "required": ["path"],
            },
            handler=data_profile_file_handler,
            category="documents",
            source_module="handlers.data_workbench",
        ),
    ]
