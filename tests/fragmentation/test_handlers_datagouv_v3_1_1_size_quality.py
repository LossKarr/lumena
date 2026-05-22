"""V3.1.1 — pénalités taille / profil acheteur dans le scoring datagouv.

Bug observé en prod :
- `marches-publics-8` a une seule ressource CSV de 172 octets (fiche profil acheteur)
- V3.1 lui donnait 100/100 et le plaçait n°1
- L'utilisateur ne pouvait rien en faire

Le scoring V3.1.1 doit :
- Pénaliser fortement les ressources < 1 KB
- Pénaliser modérément les ressources 1-10 KB
- Récompenser les ressources 10 KB - 20 MB
- Pénaliser les datasets type "profil acheteur" / DCAT-AP
- Plafonner à 90 si la taille est inconnue (pas de 100/100 à l'aveugle)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.datagouv import (
    _best_resource_size_bytes,
    _matches_poor_marker,
    _score_dataset_v3,
    datagouv_search_handler,
)


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path, runtime_root=workspace,
    )


def _recent_iso(days_ago: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).date().isoformat()


def _ds(slug, title="X", desc="", csv_size=None, formats=("csv",),
        org="INSEE", days_ago=60, extra_resources=None):
    resources = [{"format": formats[0], "filesize": csv_size} if csv_size is not None
                 else {"format": formats[0]}]
    for f in formats[1:]:
        resources.append({"format": f})
    if extra_resources:
        resources.extend(extra_resources)
    return {
        "slug": slug,
        "title": title,
        "organization": ({"name": org} if org else None),
        "description": desc,
        "last_modified": _recent_iso(days_ago),
        "resources": resources,
    }


# ─── helpers ────────────────────────────────────────────────────────────


def test_best_resource_size_filters_by_format():
    ds = {
        "resources": [
            {"format": "csv", "filesize": 1000},
            {"format": "json", "filesize": 50000},
        ]
    }
    assert _best_resource_size_bytes(ds, "csv") == 1000
    assert _best_resource_size_bytes(ds, "json") == 50000


def test_best_resource_size_none_if_no_filesize():
    ds = {"resources": [{"format": "csv"}]}
    assert _best_resource_size_bytes(ds, "csv") is None


def test_best_resource_size_falls_back_when_format_missing():
    """Si le format demandé n'a pas de resource, prend la taille la plus grosse."""
    ds = {"resources": [{"format": "xml", "filesize": 800000}]}
    assert _best_resource_size_bytes(ds, "csv") == 800000


@pytest.mark.parametrize("title_or_desc", [
    "Profil acheteur du SIVOM",
    "PROFIL-ACHETEUR Région X",
    "Fiche acheteur 2024",
    "Catalogue DCAT-AP des marchés",
    "URLProfilAcheteur exposée",
    "Métadonnées seulement du marché",
    "Coordonnées de l'acheteur public",
])
def test_poor_markers_detected(title_or_desc):
    assert _matches_poor_marker(title_or_desc) is True


@pytest.mark.parametrize("normal", [
    "Marchés publics conclus en 2024",
    "Liste des fournisseurs retenus",
    "Données budgétaires",
    "Statistiques INSEE",
])
def test_normal_titles_not_poor(normal):
    assert _matches_poor_marker(normal) is False


# ─── pénalités taille ───────────────────────────────────────────────────


def test_score_tiny_resource_penalized():
    """Fiche 172 octets → score forcément en dessous d'un dataset 800 KB."""
    tiny = _ds("tiny", csv_size=172)
    healthy = _ds("healthy", csv_size=800_000)
    s_tiny, _, _ = _score_dataset_v3(tiny, "csv")
    s_healthy, _, _ = _score_dataset_v3(healthy, "csv")
    assert s_tiny < s_healthy - 30, (
        f"172o ({s_tiny}) doit être nettement en dessous de 800KB ({s_healthy})"
    )


def test_score_small_resource_penalized():
    small = _ds("small", csv_size=5_000)  # 5 KB
    healthy = _ds("healthy", csv_size=500_000)  # 500 KB
    s_small, _, _ = _score_dataset_v3(small, "csv")
    s_healthy, _, _ = _score_dataset_v3(healthy, "csv")
    assert s_small < s_healthy


def test_score_healthy_size_bonus():
    healthy = _ds("healthy", csv_size=800_000)
    score, reasons, verdict = _score_dataset_v3(healthy, "csv")
    assert any("taille saine" in r for r in reasons)
    assert verdict.startswith("✅")


def test_score_unknown_size_capped_at_90():
    """Si la taille est inconnue, on ne donne pas 100/100 à l'aveugle."""
    unknown = _ds("unknown", csv_size=None)
    score, reasons, _ = _score_dataset_v3(unknown, "csv")
    assert score <= 90, f"Sans filesize, le score doit être plafonné à 90 (got {score})"


# ─── profil acheteur ────────────────────────────────────────────────────


def test_score_profil_acheteur_penalized():
    """Le bug exact des logs : CSV 172o + titre 'profil acheteur'."""
    profil = _ds(
        "marches-publics-8", title="Profil acheteur SIVOM",
        csv_size=172, org="SIVOM",
    )
    score, reasons, verdict = _score_dataset_v3(profil, "csv")
    # Doit être loin du seuil "✅ choisir"
    assert score < 70, f"Profil acheteur ne doit PAS être ✅ (got {score})"
    assert any("profil" in r.lower() or "fiche" in r.lower() for r in reasons)


def test_score_profil_in_description_also_detected():
    ds = _ds(
        "x", title="Marchés", desc="Ce dataset contient le profil acheteur du SIVOM",
        csv_size=200_000,
    )
    score_profil, _, _ = _score_dataset_v3(ds, "csv")
    # Comparé à un dataset normal de même taille
    normal = _ds("y", title="Marchés", desc="Liste des marchés conclus 2024",
                  csv_size=200_000)
    score_normal, _, _ = _score_dataset_v3(normal, "csv")
    assert score_profil < score_normal


# ─── scénario UI exact ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ui_scenario_hauts_de_seine_before_profil_acheteur(ctx):
    """Cible : un CSV 800 KB Hauts-de-Seine doit passer AVANT un CSV 172o profil."""
    profil_acheteur = _ds(
        "marches-publics-8",
        title="Profil acheteur du SIVOM",
        csv_size=172, org="SIVOM",
    )
    hauts_de_seine = _ds(
        "marches-hauts-de-seine",
        title="Marchés publics Hauts-de-Seine 2024",
        desc="Liste exhaustive des marchés publics du département.",
        csv_size=800_000, org="Département 92",
    )
    svc = MagicMock()
    svc.search_datasets = AsyncMock(return_value={
        "data": [profil_acheteur, hauts_de_seine],
        "total": 2,
    })
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=svc,
    ):
        result = await datagouv_search_handler(
            ctx, query="marchés publics", required_format="csv"
        )
    out = result.output
    # Hauts-de-Seine doit apparaître avant profil-acheteur
    idx_hds = out.find("marches-hauts-de-seine")
    idx_profil = out.find("marches-publics-8")
    assert idx_hds != -1 and idx_profil != -1
    assert idx_hds < idx_profil, (
        "Le dataset 800 KB doit être listé avant le dataset 172o"
    )


@pytest.mark.asyncio
async def test_output_shows_size_in_reasons(ctx):
    healthy = _ds("h", csv_size=800_000)
    svc = MagicMock()
    svc.search_datasets = AsyncMock(return_value={"data": [healthy], "total": 1})
    with patch(
        "src.reasoning.handlers.datagouv._get_service",
        return_value=svc,
    ):
        result = await datagouv_search_handler(
            ctx, query="x", required_format="csv"
        )
    out = result.output
    # taille saine présente dans les raisons
    assert "taille saine" in out.lower() or "kb" in out.lower()


# ─── garde-fou anti-cap-100 ─────────────────────────────────────────────


def test_no_100_score_without_richness_signal():
    """Un dataset sans filesize ne doit pas dépasser 90 même si tout le reste est parfait."""
    perfect_but_unknown_size = _ds(
        "p", title="Marchés publics 2024 — données complètes",
        desc=("x" * 200), org="INSEE", csv_size=None, days_ago=10,
    )
    score, _, _ = _score_dataset_v3(perfect_but_unknown_size, "csv")
    assert score <= 90
