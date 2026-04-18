"""Tests pour le pipeline_router — bypass direct de la boucle ReAct."""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from src.reasoning.pipeline_router import (
    match_pipeline, run_pipeline,
    _match_edit_and_deploy, _match_deploy_only, _match_edit_website_only,
    PipelineResult,
)

_RESOLVE_WS_PATCH = "src.utils.project_registry.resolve_workspace"


# ──────────────────────────────────────────────────────────
# Tests matchers
# ──────────────────────────────────────────────────────────

class TestMatchEditAndDeploy:
    def test_typical_request(self):
        assert _match_edit_and_deploy("améliore mon site web et déploie sur IONOS")

    def test_english(self):
        assert _match_edit_and_deploy("update my website and deploy to IONOS")

    def test_upload_variant(self):
        assert _match_edit_and_deploy("modifie le site et upload sur le serveur")

    def test_no_deploy(self):
        assert not _match_edit_and_deploy("améliore mon site web")

    def test_no_edit(self):
        assert not _match_edit_and_deploy("déploie le site sur IONOS")

    def test_no_site(self):
        assert not _match_edit_and_deploy("améliore le code et déploie")

    def test_met_en_ligne(self):
        assert _match_edit_and_deploy("change la page web et mets en ligne")


class TestMatchDeployOnly:
    def test_basic(self):
        assert _match_deploy_only("déploie le site sur IONOS")

    def test_upload(self):
        assert _match_deploy_only("upload les fichiers sur le serveur SFTP")

    def test_publie(self):
        assert _match_deploy_only("publie mon site")

    def test_not_with_edit(self):
        assert not _match_deploy_only("améliore et déploie le site")


class TestMatchEditWebsiteOnly:
    def test_basic(self):
        assert _match_edit_website_only("améliore mon site web")

    def test_path_alone_not_enough(self):
        # Un path seul sans mention de "site" ne doit PAS matcher
        assert not _match_edit_website_only("modifie C:\\workspace\\monsite les fichiers")

    def test_english(self):
        assert _match_edit_website_only("update the website design")

    def test_not_with_deploy(self):
        assert not _match_edit_website_only("améliore le site et déploie sur IONOS")

    def test_no_match_video_remotion(self):
        assert not _match_edit_website_only("fais une vidéo remotion")

    def test_no_match_pdf_request(self):
        assert not _match_edit_website_only("modifie le rapport pdf du site")

    def test_no_match_discord_action(self):
        assert not _match_edit_website_only("améliore le site discord")

    def test_still_matches_edit_site(self):
        assert _match_edit_website_only("améliore mon site web avec un nouveau design")


class TestMatchEditAndDeploySkillExclusion:
    def test_no_match_video(self):
        assert not _match_edit_and_deploy("améliore le site web vidéo et déploie sur IONOS")

    def test_still_matches_clean(self):
        assert _match_edit_and_deploy("améliore mon site web et déploie sur IONOS")


# ──────────────────────────────────────────────────────────
# Tests match_pipeline
# ──────────────────────────────────────────────────────────

class TestMatchPipeline:
    def test_edit_and_deploy(self):
        pipe = match_pipeline("améliore mon site web et déploie sur IONOS")
        assert pipe is not None
        assert pipe.name == "edit_and_deploy"

    def test_deploy_only(self):
        pipe = match_pipeline("déploie le site sur IONOS")
        assert pipe is not None
        assert pipe.name == "deploy_only"

    def test_edit_only(self):
        pipe = match_pipeline("améliore mon site web")
        assert pipe is not None
        assert pipe.name == "edit_website_only"

    def test_no_match(self):
        pipe = match_pipeline("quel temps fait-il demain ?")
        assert pipe is None

    def test_no_match_code_task(self):
        pipe = match_pipeline("crée un script Python qui trie une liste")
        assert pipe is None

    def test_priority_edit_deploy_over_edit_only(self):
        pipe = match_pipeline("modifie le site web et déploie le tout")
        assert pipe is not None
        assert pipe.name == "edit_and_deploy"


# ──────────────────────────────────────────────────────────
# Tests run_pipeline
# ──────────────────────────────────────────────────────────

class TestRunPipeline:
    @pytest.fixture
    def mock_registry(self):
        reg = MagicMock()
        obs = MagicMock()
        obs.success = True
        obs.content = "OK - fichiers modifiés"
        reg.execute = AsyncMock(return_value=obs)
        return reg

    @pytest.mark.asyncio
    async def test_edit_and_deploy_success(self, mock_registry):
        pipe = match_pipeline("améliore mon site web et déploie sur openlumena.com")
        assert pipe is not None
        with patch(_RESOLVE_WS_PATCH) as mock_resolve:
            mock_ws = MagicMock()
            mock_ws.path = "C:\\workspace\\site"
            mock_resolve.return_value = mock_ws
            result = await run_pipeline(pipe, "améliore mon site web et déploie sur openlumena.com", mock_registry)
        assert result.success
        assert result.steps_executed == 2
        assert mock_registry.execute.call_count == 2

    @pytest.mark.asyncio
    async def test_deploy_only_success(self, mock_registry):
        pipe = match_pipeline("déploie sur IONOS")
        assert pipe is not None
        with patch(_RESOLVE_WS_PATCH) as mock_resolve:
            mock_ws = MagicMock()
            mock_ws.path = "C:\\workspace\\site"
            mock_resolve.return_value = mock_ws
            result = await run_pipeline(pipe, "déploie sur IONOS", mock_registry)
        assert result.success
        assert result.steps_executed == 1

    @pytest.mark.asyncio
    async def test_step_failure_returns_error(self, mock_registry):
        obs_fail = MagicMock()
        obs_fail.success = False
        obs_fail.content = "SFTP connection refused"
        mock_registry.execute = AsyncMock(return_value=obs_fail)

        pipe = match_pipeline("déploie sur IONOS")
        with patch(_RESOLVE_WS_PATCH) as mock_resolve:
            mock_ws = MagicMock()
            mock_ws.path = "C:\\workspace\\site"
            mock_resolve.return_value = mock_ws
            result = await run_pipeline(pipe, "déploie sur IONOS", mock_registry)
        assert not result.success
        assert "SFTP" in result.message

    @pytest.mark.asyncio
    async def test_no_project_dir_returns_error(self, mock_registry):
        pipe = match_pipeline("déploie sur IONOS")
        with patch(_RESOLVE_WS_PATCH) as mock_resolve:
            mock_ws = MagicMock()
            mock_ws.path = None
            mock_resolve.return_value = mock_ws
            result = await run_pipeline(pipe, "déploie sur IONOS", mock_registry)
        assert not result.success

    @pytest.mark.asyncio
    async def test_plan_callback_called(self, mock_registry):
        pipe = match_pipeline("déploie sur IONOS")
        callback = MagicMock()
        with patch(_RESOLVE_WS_PATCH) as mock_resolve:
            mock_ws = MagicMock()
            mock_ws.path = "C:\\workspace\\site"
            mock_resolve.return_value = mock_ws
            await run_pipeline(pipe, "déploie sur IONOS", mock_registry, plan_callback=callback)
        assert callback.call_count >= 2  # initial + at least 1 step

    @pytest.mark.asyncio
    async def test_domain_extraction(self, mock_registry):
        pipe = match_pipeline("déploie sur openlumena.com")
        with patch(_RESOLVE_WS_PATCH) as mock_resolve:
            mock_ws = MagicMock()
            mock_ws.path = "C:\\workspace\\site"
            mock_resolve.return_value = mock_ws
            await run_pipeline(pipe, "déploie sur openlumena.com", mock_registry)
        call_args = mock_registry.execute.call_args
        assert call_args[0][1].get("site") == "openlumena.com"


# ──────────────────────────────────────────────────────────
# P0 Fix: intent destructif + deploy-as-noun guards
# ──────────────────────────────────────────────────────────

class TestDestructiveIntentGuard:
    """Requêtes de suppression/retrait ne doivent JAMAIS déclencher un pipeline."""

    def test_supprime_section_deploiement(self):
        """Bug prod: 'supprime la partie Déploiement Automatique' déclenchait deploy_only."""
        assert not _match_deploy_only("supprime la partie Déploiement Automatique sur le site")
        assert match_pipeline("supprime la partie Déploiement Automatique sur le site") is None

    def test_enleve_section_deploy(self):
        assert not _match_edit_and_deploy("enlève la section deploy du site et redéploie")
        assert not _match_deploy_only("enlève la section deploy")

    def test_retire_module(self):
        assert match_pipeline("retire le module de déploiement du site web") is None

    def test_efface_page(self):
        assert match_pipeline("efface la page de déploiement du site") is None

    def test_delete_deploy_section(self):
        assert match_pipeline("delete the deployment section from the website") is None

    def test_remove_deploy(self):
        assert match_pipeline("remove the deploy page from my site") is None

    def test_supprime_sans_deploy_site(self):
        """Supprime sans contexte site → pas de pipeline (aucun matcher)."""
        assert match_pipeline("supprime ce fichier") is None

    def test_desactive_deploiement(self):
        assert match_pipeline("désactive le déploiement automatique sur le site") is None

    def test_nettoie_site(self):
        assert match_pipeline("nettoie le site web et enlève les sections inutiles") is None


class TestDeployAsNounGuard:
    """'Déploiement' comme nom de section ≠ verbe 'déploie'."""

    def test_partie_deploiement(self):
        """'la partie Déploiement' → noun, pas un verbe."""
        assert not _match_deploy_only("modifie la partie Déploiement Automatique")

    def test_section_deploy(self):
        assert not _match_deploy_only("la section de déploiement est obsolète")

    def test_titre_deploiement(self):
        assert not _match_deploy_only("change le titre déploiement continu")

    def test_vrai_verbe_deploie_toujours(self):
        """Un vrai verbe 'déploie' continue de matcher."""
        assert _match_deploy_only("déploie le site sur IONOS")

    def test_upload_toujours(self):
        """'upload' est toujours un verbe d'action."""
        assert _match_deploy_only("upload les fichiers sur SFTP")

    def test_publie_toujours(self):
        assert _match_deploy_only("publie mon site web")

    def test_edit_and_deploy_still_works(self):
        """Les vrais edit+deploy continuent de fonctionner."""
        assert _match_edit_and_deploy("améliore mon site web et déploie sur IONOS")

    def test_edit_only_still_works(self):
        assert _match_edit_website_only("améliore mon site web")
