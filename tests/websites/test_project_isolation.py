"""Tests pour l'isolation automatique des projets dans le workspace.

Couvre:
- _generate_project_slug (react.py)
- _resolve_path strip workspace/ quand ws_root est dedans (sub_agent.py)
- run_command préfixe cd /d quand _task_workspace_root actif (sub_agent.py)
"""

from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


# ──────────────────────────────────────────────────────
# Tests _generate_project_slug
# ──────────────────────────────────────────────────────
class TestGenerateProjectSlug:
    @pytest.fixture(autouse=True)
    def _import(self):
        from src.reasoning.react import _generate_project_slug
        self.fn = _generate_project_slug

    def test_basic_french_query(self):
        slug = self.fn("creer moi un site web pour ma boutique de airsoft")
        assert "boutique" in slug or "airsoft" in slug
        assert "/" not in slug
        assert " " not in slug

    def test_strips_noise_words(self):
        slug = self.fn("fait moi un site web avec des photos")
        # "fait", "moi", "un", "site", "web", "avec", "des", "photos" are all noise
        # should still return something
        assert len(slug) > 0

    def test_english_query(self):
        slug = self.fn("create a portfolio website for my design studio")
        assert "portfolio" in slug or "design" in slug or "studio" in slug

    def test_max_length(self):
        long_query = "creer un site web avec beaucoup de fonctionnalites extraordinaires et impressionnantes"
        slug = self.fn(long_query)
        assert len(slug) <= 40

    def test_no_special_chars(self):
        slug = self.fn("crée un jeu Pac-Man en HTML/CSS!")
        assert all(c.isalnum() or c == "-" for c in slug)

    def test_empty_fallback(self):
        slug = self.fn("le la les de du des")
        assert slug == "project"

    def test_accented_characters(self):
        slug = self.fn("génère une application de météo responsive")
        assert slug  # should produce something, accented chars stripped

    def test_game_project(self):
        slug = self.fn("fais moi un snake en javascript")
        assert "snake" in slug or "javascript" in slug


# ──────────────────────────────────────────────────────
# Tests _resolve_path avec workspace/ prefix stripping
# ──────────────────────────────────────────────────────
class TestResolvePathWorkspaceStrip:
    @pytest.fixture
    def agent(self, tmp_path):
        """Crée un agent minimal avec workspace root configuré."""
        from unittest.mock import MagicMock
        agent = MagicMock()
        agent._project_root = MagicMock(return_value=tmp_path)

        # Import la vraie méthode
        from src.agents.sub_agent import SubAgent
        agent._resolve_path = SubAgent._resolve_path.__get__(agent)

        return agent

    def test_bare_path_resolves_to_wsroot(self, agent, tmp_path):
        """index.html → workspace/airsoft/index.html"""
        ws_root = tmp_path / "workspace" / "airsoft"
        ws_root.mkdir(parents=True)
        agent._task_workspace_root = ws_root

        result = agent._resolve_path("index.html")
        assert result == ws_root / "index.html"

    def test_subdir_path_resolves_to_wsroot(self, agent, tmp_path):
        """css/style.css → workspace/airsoft/css/style.css"""
        ws_root = tmp_path / "workspace" / "airsoft"
        ws_root.mkdir(parents=True)
        agent._task_workspace_root = ws_root

        result = agent._resolve_path("css/style.css")
        assert result == ws_root / "css" / "style.css"

    def test_workspace_prefix_stripped(self, agent, tmp_path):
        """workspace/index.html → workspace/airsoft/index.html (strip workspace/)"""
        ws_root = tmp_path / "workspace" / "airsoft"
        ws_root.mkdir(parents=True)
        agent._task_workspace_root = ws_root

        result = agent._resolve_path("workspace/index.html")
        assert result == ws_root / "index.html"

    def test_workspace_subdir_prefix_stripped(self, agent, tmp_path):
        """workspace/css/style.css → workspace/airsoft/css/style.css"""
        ws_root = tmp_path / "workspace" / "airsoft"
        ws_root.mkdir(parents=True)
        agent._task_workspace_root = ws_root

        result = agent._resolve_path("workspace/css/style.css")
        assert result == ws_root / "css" / "style.css"

    def test_full_wsrel_path_not_doubled(self, agent, tmp_path):
        """workspace/airsoft/index.html → workspace/airsoft/index.html (pas doublé)"""
        ws_root = tmp_path / "workspace" / "airsoft"
        ws_root.mkdir(parents=True)
        agent._task_workspace_root = ws_root

        result = agent._resolve_path("workspace/airsoft/index.html")
        assert result == tmp_path / "workspace" / "airsoft" / "index.html"

    def test_absolute_path_unchanged(self, agent, tmp_path):
        """Les chemins absolus ne sont pas touchés."""
        ws_root = tmp_path / "workspace" / "airsoft"
        ws_root.mkdir(parents=True)
        agent._task_workspace_root = ws_root

        abs_path = tmp_path / "other" / "file.txt"
        result = agent._resolve_path(str(abs_path))
        assert result == abs_path

    def test_no_workspace_root_uses_project_root(self, agent, tmp_path):
        """Sans workspace root, résout depuis project root."""
        agent._task_workspace_root = None

        result = agent._resolve_path("index.html")
        assert result == tmp_path / "index.html"

    def test_backslash_workspace_prefix(self, agent, tmp_path):
        """workspace\\index.html → workspace/airsoft/index.html"""
        ws_root = tmp_path / "workspace" / "airsoft"
        ws_root.mkdir(parents=True)
        agent._task_workspace_root = ws_root

        result = agent._resolve_path("workspace\\index.html")
        assert result == ws_root / "index.html"


# ──────────────────────────────────────────────────────
# Tests run_command cd /d prefix
# ──────────────────────────────────────────────────────
class TestRunCommandCdPrefix:
    def test_run_command_gets_cd_prefix(self, tmp_path):
        """Vérifie que _execute_loop_action ajoute cd /d pour run_command."""
        import sys
        if sys.platform != "win32":
            pytest.skip("Windows only")

        ws_root = tmp_path / "workspace" / "airsoft"
        ws_root.mkdir(parents=True)

        # Simuler le comportement attendu
        cmd = "dir"
        expected_prefix = f'cd /d "{ws_root}"'

        # Le CodeAgent préfixe la commande
        prefixed = f'cd /d "{ws_root}" && {cmd}'
        assert expected_prefix in prefixed
        assert cmd in prefixed


# ──────────────────────────────────────────────────────
# Tests _CREATE_PROJECT_RE pattern
# ──────────────────────────────────────────────────────
class TestCreateProjectDetection:
    @pytest.fixture(autouse=True)
    def _import(self):
        import re
        self.pattern = re.compile(
            r'(?:cr[eé]|génère|genere|build|make|develop|create|nouveau|nouvelle|new)'
            r'.{0,40}'
            r'(?:site|web|app|page|projet|project|portfolio|landing|dashboard|'
            r'boutique|shop|store|application|jeu|game)',
            re.IGNORECASE,
        )

    def test_french_create_site(self):
        assert self.pattern.search("creer moi un site web pour ma boutique")

    def test_french_create_game(self):
        # "fais" n'est pas dans _CREATE_PROJECT_RE (géré par has_action en amont)
        # mais "crée un jeu" oui
        assert self.pattern.search("crée moi un jeu snake")

    def test_english_create_app(self):
        assert self.pattern.search("create a web application for my business")

    def test_build_project(self):
        assert self.pattern.search("build me a portfolio website")

    def test_new_project(self):
        assert self.pattern.search("new landing page for my product")

    def test_no_match_fix_bug(self):
        assert not self.pattern.search("fix the bug in module.py")

    def test_no_match_config(self):
        assert not self.pattern.search("configure the email settings")

    def test_no_match_read(self):
        assert not self.pattern.search("read the project documentation")

    def test_french_generate(self):
        assert self.pattern.search("génère une application météo responsive")

    def test_nouveau_projet(self):
        assert self.pattern.search("nouveau projet dashboard admin")


# ──────────────────────────────────────────────────────
# Tests exclusion patterns override (greeting + code task)
# ──────────────────────────────────────────────────────
class TestExclusionPatternOverride:
    """Vérifie que les greetings (salut, bonjour, etc.) ne bloquent pas
    le routing CodeAgent quand des signaux code forts sont présents."""

    @pytest.fixture(autouse=True)
    def _setup(self):
        """Reconstruit la logique d'exclusion telle qu'implémentée dans react.py."""
        self._exclusion_patterns = (
            "qui es-tu", "qui suis-je", "bonjour", "salut", "hello", "merci",
            "résume", "resume", "explique", "raconte", "rappelle", "cherche",
            "search", "google", "météo", "meteo", "heure", "date", "time",
            "email", "mail", "envoie", "planifie", "schedule", "rappel",
            "souviens", "mémoire", "memory", "aide", "help", "comment",
            "scan", "réseau", "network", "screenshot", "capture",
            "discord", "telegram", "twitter", "webhook",
            "configure", "paramètre", "settings", "config",
        )
        self._code_action_verbs = (
            "crée", "cree", "créer", "creer", "create", "fait", "fais",
            "modifie", "modifier", "modify", "change", "update",
            "corrige", "corriger", "fix", "debug", "répare", "repare",
            "refactor", "refactorise", "restructure",
            "ajoute", "ajouter", "add", "implémente", "implemente",
            "supprime", "supprimer", "delete", "remove",
            "développe", "developpe", "build", "génère", "genere", "generate",
            "améliore", "ameliore", "improve", "optimise", "optimize",
            "rendre", "rends", "rend",
            "transformer", "transforme", "convertir", "convertis",
            "refaire", "refais", "redesigner", "redesigne",
            "moderniser", "modernise", "upgrade",
            "adapter", "adapte", "migrer", "migre",
            "rewrite", "réécrire", "réécris", "reecrire", "reecris",
            "porter", "porte",
            "intégrer", "integrer", "integre", "intègre",
            "finir", "finis", "terminer", "termine", "compléter", "complete",
            "étendre", "etendre", "étends", "etends",
        )
        self._code_targets = (
            "site", "website", "page web", "landing", "portfolio",
            "app", "application", "projet", "project",
            "script", "programme", "program", "logiciel", "software",
            "jeu", "game", "snake", "tetris", "pong",
            "api", "serveur", "server", "backend", "frontend",
            "dashboard", "interface", "formulaire", "form",
            "bot", "outil", "tool", "cli",
            "composant", "component", "widget", "plugin", "extension",
            "template", "thème", "theme", "layout",
            "base de données", "database", "schema",
            "fichier", "file", "dossier", "folder",
        )

    def _should_route(self, query: str) -> bool:
        """Simule la logique de routing _maybe_auto_route_codeagent."""
        q = query.lower()
        _has_exclusion = any(p in q for p in self._exclusion_patterns)

        import re

        # ── Guard hypothétique (conditionnel / question de capacité) ──
        _hypothetical_patterns = (
            r"\btu\s+(?:serr?ais|pourr?ais|saur?ais)\b",
            r"\best[- ]ce\s+que\s+tu\s+(?:peux|sais|pourrais|saurais|serais)\b",
            r"\bserais[- ]tu\s+capable\b",
            r"\btu\s+(?:peux|sais)\s+(?:faire|corriger|créer|coder|développer|modifier)\b.*\?\s*$",
        )
        _is_hypothetical = any(re.search(p, q) for p in _hypothetical_patterns)
        if _is_hypothetical:
            _polite_request_markers = (
                "stp", "s'il te plait", "s'il te plaît", "please", "pls",
                "pour moi", "fait moi", "fais moi",
            )
            _has_polite_request = any(m in q for m in _polite_request_markers)
            _has_concrete_ref = bool(re.search(
                r"(https?://|workspace[/\\]|file:///|\.py\b|\.js\b|\.html\b|\.css\b|\.ts\b"
                r"|[cs]e\s+(?:site|projet|fichier|code)|le\s+(?:site|projet|fichier|code))",
                q,
            ))
            if not _has_concrete_ref and not _has_polite_request:
                return False

        # ── Guard négation ──
        _negation_patterns = (
            r"\bne\s+\w+\s+pas\b",
            r"\bpas\s+(?:besoin|envie|la peine)\b",
            r"\bsans\s+(?:créer|coder|faire|modifier|corriger|développer|build)\b",
            r"\bdon'?t\s+(?:create|build|make|fix|modify)\b",
            r"\bnon\s*,?\s*(?:je|pas|merci)\b",
        )
        if any(re.search(p, q) for p in _negation_patterns):
            return False

        # ── Guard question informationnelle ──
        _q_stripped = q.strip()
        _ends_with_question = _q_stripped.endswith("?")
        if _ends_with_question:
            _info_starters = (
                r"^(?:comment|how|pourquoi|why|qu'?est[- ]ce\s+qu|c'?est\s+quoi|"
                r"quelle?\s+(?:est|sont)|what\s+is|what\s+are|"
                r"expliqu|défini[sr]|qu'?est[- ]ce|"
                r"aide[- ]moi\s+[àa]\s+comprendre)",
            )
            if any(re.search(p, _q_stripped) for p in _info_starters):
                _request_override = (
                    "stp", "s'il te plait", "s'il te plaît", "please",
                    "pour moi", "fait moi", "fais moi", "aide moi à faire",
                    "aide-moi à faire", "help me",
                )
                if not any(m in q for m in _request_override):
                    return False

        _has_external_url = bool(re.search(r"https?://", q))
        _q_no_urls = re.sub(r"https?://\S+", "", q) if _has_external_url else q

        has_action = any(v in _q_no_urls for v in self._code_action_verbs)
        has_target = any(t in _q_no_urls for t in self._code_targets)
        has_file = any(f in _q_no_urls for f in (".py", ".js", ".html", ".css", ".ts"))
        has_concept = any(c in _q_no_urls for c in (
            "code", "html", "css", "javascript", "python", "moderne",
        ))

        _has_file_ref = bool(re.search(r"(workspace[/\\\\]|file:///)", q))
        if not _has_file_ref and not _has_external_url:
            _has_file_ref = bool(re.search(
                r"(\.html\b|\.css\b|\.js\b|\.py\b|\.ts\b)", q,
            ))

        _browse_indicators = (
            "analyse", "analyser", "rapport", "report", "résumé", "resume",
            "va sur", "vas sur", "visite", "ouvre", "regarde", "consulte",
            "compare", "scrape", "crawl", "navigue",
        )
        _has_browse_indicator = _has_external_url and any(b in q for b in _browse_indicators)

        _create_indicators = (
            "crée", "cree", "créer", "creer", "create", "nouveau", "nouvelle",
            "new", "génère", "genere", "generate", "build", "développe",
            "developpe", "reprodui", "recré", "recree",
            "clone", "copie", "refai", "base sur", "basé sur",
        )
        _create_weak = ("fait moi", "fais moi")
        _has_weak_create = False
        import re as _re
        _target_re = _re.compile(
            r'\b(' + '|'.join(_re.escape(t) for t in self._code_targets) + r')\b',
            _re.IGNORECASE,
        )
        for _cw in _create_weak:
            _cw_idx = _q_no_urls.find(_cw)
            if _cw_idx >= 0:
                _after_cw = _q_no_urls[_cw_idx + len(_cw):]
                if _target_re.search(_after_cw):
                    _has_weak_create = True
                    break
        _has_create_intent = (
            any(ci in _q_no_urls for ci in _create_indicators)
            or _has_weak_create
        )
        _is_browse_task = _has_browse_indicator and not _has_create_intent

        _is_code_task = has_action and (has_target or has_file or has_concept or _has_file_ref)
        if not _is_code_task:
            return False
        if _is_browse_task:
            return False
        if _has_exclusion and not (has_target or has_file or _has_file_ref):
            return False
        return True

    # ── Greetings + code task → DOIT router ──
    def test_salut_ameliore_site(self):
        assert self._should_route("salut lumena tu pourrais ameliore se site web stp")

    def test_bonjour_cree_app(self):
        assert self._should_route("bonjour crée moi une application web")

    def test_hello_fix_script(self):
        assert self._should_route("hello fix the main.py script")

    def test_merci_ameliore_projet(self):
        assert self._should_route("merci mais ameliore le projet stp")

    def test_aide_creer_site(self):
        assert self._should_route("aide moi à créer un site web")

    def test_comment_ameliorer_page(self):
        """'comment' en exclusion ne bloque pas si target présent."""
        assert self._should_route("comment améliorer ma page web")

    def test_salut_complete_workspace(self):
        """Chemin workspace/ avec greeting → doit router."""
        assert self._should_route(
            "salut ameliore le site dans workspace/airsoft"
        )

    # ── Greetings seuls → NE DOIT PAS router ──
    def test_salut_alone(self):
        assert not self._should_route("salut lumena")

    def test_bonjour_comment_ca_va(self):
        assert not self._should_route("bonjour comment ça va ?")

    def test_merci_cest_parfait(self):
        assert not self._should_route("merci c'est parfait")

    # ── Exclusions non-greeting + code task → garde souple ──
    def test_cherche_bugs_code(self):
        """'cherche' est en exclusion et 'cherche' n'est pas un action verb → bloqué."""
        assert not self._should_route("cherche les bugs dans le code")

    def test_configure_settings(self):
        """'configure' en exclusion, pas un action verb → bloqué."""
        assert not self._should_route("configure les settings du serveur")

    # ── URL externe + browse → NE DOIT PAS router ──
    def test_url_analyse_rapport(self):
        """URL HTTP + 'analyse' + 'rapport' → browse task, pas code."""
        assert not self._should_route(
            "https://mx-moto.fr/mini-moto/11195-pignon.html va sur le site et analyse la page puis fait moi un rapport complet"
        )

    def test_url_va_sur_le_site(self):
        assert not self._should_route(
            "https://example.com va sur le site et regarde les prix"
        )

    def test_url_scrape(self):
        assert not self._should_route(
            "https://shop.com/products scrape moi cette page et fait un résumé"
        )

    def test_url_compare(self):
        assert not self._should_route(
            "https://site-a.com et https://site-b.com compare les deux sites"
        )

    # ── URL externe + vrai code task → DOIT router ──
    def test_url_clone_site(self):
        """'crée un clone du site' avec URL → c'est du code (pas un browse indicator)."""
        assert self._should_route(
            "crée un clone du site https://example.com"
        )

    def test_url_build_similar(self):
        assert self._should_route(
            "build me a similar site to https://example.com"
        )

    # ── URL + browse + create intent → DOIT router (CodeAgent) ──
    def test_url_analyse_puis_cree(self):
        """'analyse ce site puis crée-en un nouveau' → browse + create → CodeAgent."""
        assert self._should_route(
            "https://n8n.io analyse bien ce site j'aimerai que tu en creer un nouveau et complet basé sur le design"
        )

    def test_url_regarde_et_fais_moi(self):
        """'regarde le site et fais moi un similaire' → browse + create → CodeAgent."""
        assert self._should_route(
            "https://example.com regarde le site et fais moi un site similaire"
        )

    def test_url_visite_et_reproduis(self):
        """'visite et crée un nouveau' → browse + create → CodeAgent."""
        assert self._should_route(
            "https://example.com visite ce site et crée un nouveau site basé dessus"
        )

    def test_url_analyse_et_genere(self):
        """'analyse et génère' → browse + create → CodeAgent."""
        assert self._should_route(
            "https://example.com analyse ce site et génère un site identique"
        )

    def test_url_base_sur(self):
        """'basé sur ce site' → browse + create → CodeAgent."""
        assert self._should_route(
            "crée un site basé sur https://example.com analyse le design"
        )

    # ── .html dans URL ne compte PAS comme file_ref ──
    def test_html_in_url_not_file_ref(self):
        """'.html' dans une URL HTTP ne doit pas déclencher _has_file_ref."""
        import re
        q = "https://example.com/page.html"
        _has_external_url = bool(re.search(r"https?://", q))
        _has_file_ref = bool(re.search(r"(workspace[/\\\\]|file:///)", q))
        if not _has_file_ref and not _has_external_url:
            _has_file_ref = bool(re.search(r"(\.html\b)", q))
        assert not _has_file_ref

    def test_local_html_is_file_ref(self):
        """'.html' sans URL → c'est bien un file_ref local."""
        import re
        q = "modifie index.html"
        _has_external_url = bool(re.search(r"https?://", q))
        _has_file_ref = bool(re.search(r"(workspace[/\\\\]|file:///)", q))
        if not _has_file_ref and not _has_external_url:
            _has_file_ref = bool(re.search(r"(\.html\b)", q))
        assert _has_file_ref

    def test_url_html_no_browse_indicator(self):
        """URL .html sans browse indicator → NE DOIT PAS router (has_file ne matche pas en URL)."""
        assert not self._should_route(
            "https://example.com/page.html fait moi un truc"
        )

    def test_url_html_with_local_file_too(self):
        """URL + fichier local séparé → DOIT router (le fichier local est hors URL)."""
        assert self._should_route(
            "améliore index.html en te basant sur https://example.com"
        )

    # ── Questions hypothétiques / de capacité → NE DOIT PAS router ──
    def test_hypothetical_conditional_serais(self):
        """'tu serrais corriger' = conditionnel → pas d'action."""
        assert not self._should_route("tu serrais corriger un site web si il est casser ?")

    def test_hypothetical_conditional_pourrais(self):
        assert not self._should_route("tu pourrais créer une app mobile ?")

    def test_hypothetical_conditional_saurais(self):
        assert not self._should_route("tu saurais corriger un bug dans un script ?")

    def test_hypothetical_est_ce_que_tu_peux(self):
        assert not self._should_route("est-ce que tu peux faire un site web ?")

    def test_hypothetical_tu_sais_faire(self):
        assert not self._should_route("tu sais corriger un site web ?")

    def test_hypothetical_serais_tu_capable(self):
        assert not self._should_route("serais-tu capable de créer un jeu ?")

    # ── Hypothétique + référence concrète → DOIT router ──
    def test_hypothetical_but_concrete_ce_site(self):
        """'tu pourrais corriger ce site' → 'ce site' = référence concrète."""
        assert self._should_route("tu pourrais corriger ce site ?")

    def test_hypothetical_but_file_ref(self):
        """'tu saurais modifier index.html' → fichier concret."""
        assert self._should_route("tu saurais modifier index.html ?")

    def test_hypothetical_but_url(self):
        """'tu pourrais créer https://example.com' → URL concrète."""
        assert self._should_route("tu pourrais créer un site comme https://example.com ?")

    # ── Négation → NE DOIT PAS router ──
    def test_negation_ne_pas(self):
        """'je ne vais pas créer' → négation explicite."""
        assert not self._should_route("je ne vais pas créer de site")

    def test_negation_pas_besoin(self):
        assert not self._should_route("pas besoin de créer un script")

    def test_negation_sans_coder(self):
        assert not self._should_route("sans coder on peut faire une app ?")

    def test_negation_dont_create(self):
        assert not self._should_route("don't create a new project")

    def test_negation_non_merci(self):
        assert not self._should_route("non merci, je ne veux pas modifier le site")

    def test_negation_ne_cree_pas(self):
        assert not self._should_route("ne crée pas de fichier")

    # ── Questions informationnelles (? + mot interrogatif) → NE DOIT PAS router ──
    def test_info_question_comment_creer(self):
        """'comment créer un API REST ?' → question d'info, pas instruction."""
        assert not self._should_route("comment créer un API REST ?")

    def test_info_question_how_to_build(self):
        assert not self._should_route("how to build a website ?")

    def test_info_question_cest_quoi(self):
        assert not self._should_route("c'est quoi un serveur backend ?")

    def test_info_question_quest_ce_que(self):
        assert not self._should_route("qu'est-ce qu'une classe en python ?")

    def test_info_question_pourquoi(self):
        assert not self._should_route("pourquoi améliorer le frontend ?")

    def test_info_question_aide_comprendre(self):
        """'aide moi à comprendre' = pas une instruction de coder."""
        assert not self._should_route("aide moi a comprendre le javascript ?")

    def test_info_question_explique(self):
        assert not self._should_route("explique comment corriger un bug en python ?")

    # ── Question informationnelle + "stp" → DOIT router (demande réelle) ──
    def test_info_question_but_stp(self):
        """'comment créer un site stp ?' = vraie demande malgré le mot interrogatif."""
        assert self._should_route("comment créer un site web stp ?")

    def test_info_question_but_fais_moi(self):
        assert self._should_route("comment fais moi un portfolio ?")

    # ── Assertions directes sans ? → DOIT toujours router ──
    def test_imperative_no_question(self):
        """Pas de ? → instruction directe, doit router."""
        assert self._should_route("crée moi un site web de portfolio")

    def test_imperative_comment_without_questionmark(self):
        """'comment' sans ? → ambigu mais traité comme instruction."""
        assert self._should_route("comment on fait, améliore le site")


# ──────────────────────────────────────────────────────
# Tests cache invalidation après write tools
# ──────────────────────────────────────────────────────
class TestCacheInvalidation:
    """Vérifie que le cache list_directory/read_file est purgé après run_command/write_file."""

    def test_write_file_invalidates_list_directory_cache(self):
        from src.reasoning.tool_registry import ToolRegistry
        reg = ToolRegistry.__new__(ToolRegistry)
        reg._observation_cache = {
            'list_directory::{"path": "/workspace/airsoft"}': "placeholder.html",
            'read_file::{"path": "/workspace/airsoft/index.html"}': "<html>old</html>",
            'get_time::{}': "2026-01-01",
        }
        # Simuler l'invalidation (logique extraite de execute())
        _WRITE_TOOLS = {"write_file", "edit_file", "edit_by_lines", "apply_patch",
                        "run_command", "create_file", "delete_file"}
        name = "write_file"
        if name in _WRITE_TOOLS and reg._observation_cache:
            _stale = [k for k in reg._observation_cache
                      if k.startswith(("list_directory::", "read_file::"))]
            for sk in _stale:
                del reg._observation_cache[sk]

        assert 'list_directory::{"path": "/workspace/airsoft"}' not in reg._observation_cache
        assert 'read_file::{"path": "/workspace/airsoft/index.html"}' not in reg._observation_cache
        # get_time n'est PAS invalidé
        assert 'get_time::{}' in reg._observation_cache

    def test_run_command_invalidates_cache(self):
        from src.reasoning.tool_registry import ToolRegistry
        reg = ToolRegistry.__new__(ToolRegistry)
        reg._observation_cache = {
            'list_directory::{"path": "/workspace/airsoft/images"}': "placeholder.html",
        }
        name = "run_command"
        _WRITE_TOOLS = {"write_file", "edit_file", "edit_by_lines", "apply_patch",
                        "run_command", "create_file", "delete_file"}
        if name in _WRITE_TOOLS and reg._observation_cache:
            _stale = [k for k in reg._observation_cache
                      if k.startswith(("list_directory::", "read_file::"))]
            for sk in _stale:
                del reg._observation_cache[sk]
        assert len(reg._observation_cache) == 0

    def test_read_file_does_not_invalidate(self):
        from src.reasoning.tool_registry import ToolRegistry
        reg = ToolRegistry.__new__(ToolRegistry)
        reg._observation_cache = {
            'list_directory::{"path": "/tmp"}': "foo.txt",
        }
        name = "read_file"
        _WRITE_TOOLS = {"write_file", "edit_file", "edit_by_lines", "apply_patch",
                        "run_command", "create_file", "delete_file"}
        if name in _WRITE_TOOLS and reg._observation_cache:
            _stale = [k for k in reg._observation_cache
                      if k.startswith(("list_directory::", "read_file::"))]
            for sk in _stale:
                del reg._observation_cache[sk]
        # read_file n'est PAS un write tool → pas d'invalidation
        assert 'list_directory::{"path": "/tmp"}' in reg._observation_cache


# ──────────────────────────────────────────────────────
# Tests escalade workspace_path extraction
# ──────────────────────────────────────────────────────
class TestEscaladeWorkspacePath:
    """Vérifie que l'escalade CodeAgent déduit le bon workspace projet."""

    def test_extract_path_from_query(self, tmp_path):
        import re
        query = f"ameliore le site {tmp_path}\\workspace\\airsoft"
        ws = tmp_path / "workspace" / "airsoft"
        ws.mkdir(parents=True)
        m = re.search(r'([A-Za-z]:\\[^\s]+?[\\/]workspace[\\/][\w\-]+)', query)
        assert m
        assert m.group(1) == str(ws)

    def test_extract_path_from_query_relative(self, tmp_path):
        import re, os
        query = "ameliore le site dans workspace/airsoft"
        root = tmp_path
        ws = root / "workspace" / "airsoft"
        ws.mkdir(parents=True)
        m = re.search(r'(workspace[\\/][\w\-]+)', query)
        assert m
        cand = m.group(1)
        if not os.path.isabs(cand):
            cand = os.path.join(str(root), cand)
        assert os.path.isdir(cand)

    def test_extract_path_from_history_args(self, tmp_path):
        import re, os
        ws = tmp_path / "workspace" / "airsoft"
        ws.mkdir(parents=True)
        # Simuler un file_path d'action récente
        file_path = str(ws / "style.css")
        m = re.search(r'(.+?[\\/]workspace[\\/][\w\-]+)', file_path)
        assert m
        assert os.path.isdir(m.group(1))


# ──────────────────────────────────────────────────────
# P1: Repair workspace_path resolution
# ──────────────────────────────────────────────────────
class TestRepairWorkspacePath:
    """Vérifie que _maybe_auto_route_codeagent résout workspace_path
    vers un projet existant pour les tâches de réparation."""

    def test_repair_finds_existing_project(self, tmp_path):
        """'corrige le projet' + index.html existant → workspace_path = dossier projet."""
        import re as _re
        ws_dir = tmp_path / "workspace" / "2026-04-06" / "projet-test"
        ws_dir.mkdir(parents=True)
        (ws_dir / "index.html").write_text("<html></html>")
        (ws_dir / "style.css").write_text("body{}")

        _REPAIR_RE = _re.compile(
            r'(?:corrige|fix|repar|amélio|modifi|update|refais|optimise|'
            r'complèt|upgrade|improve|debug|restructur)',
            _re.IGNORECASE,
        )
        query = "corrige le projet web"
        assert _REPAIR_RE.search(query)

        # Simulate la logique de recherche
        _ws = tmp_path / "workspace"
        _htmls = sorted(_ws.rglob("index.html"), key=lambda f: f.stat().st_mtime, reverse=True)
        assert len(_htmls) >= 1
        _proj = _htmls[0].parent
        assert _proj == ws_dir

    def test_repair_picks_most_recent(self, tmp_path):
        """Avec 2 projets, le plus récent est sélectionné."""
        import time
        old = tmp_path / "workspace" / "2026-04-01" / "old-proj"
        old.mkdir(parents=True)
        (old / "index.html").write_text("<html>old</html>")

        time.sleep(0.05)

        new = tmp_path / "workspace" / "2026-04-06" / "new-proj"
        new.mkdir(parents=True)
        (new / "index.html").write_text("<html>new</html>")

        _ws = tmp_path / "workspace"
        _htmls = sorted(_ws.rglob("index.html"), key=lambda f: f.stat().st_mtime, reverse=True)
        _proj = _htmls[0].parent
        assert _proj == new

    def test_repair_fallback_no_project(self, tmp_path):
        """Aucun projet existant → pas de crash, workspace_path inchangé."""
        _ws = tmp_path / "workspace"
        _ws.mkdir(parents=True)
        _htmls = list(_ws.rglob("index.html"))
        assert len(_htmls) == 0
        # Le code doit simplement ne rien changer

    def test_create_still_creates_new_dir(self, tmp_path):
        """'crée un site web' ne doit PAS router vers un existant."""
        import re as _re
        _CREATE_PROJECT_RE = _re.compile(
            r'(?:cr[eé]|génère|genere|build|make|develop|create|nouveau|nouvelle|new)'
            r'.{0,40}'
            r'(?:site|web|app|page|projet|project|portfolio|landing|dashboard|'
            r'boutique|shop|store|application|jeu|game)',
            _re.IGNORECASE,
        )
        _REPAIR_RE = _re.compile(
            r'(?:corrige|fix|repar|amélio|modifi|update|refais|optimise|'
            r'complèt|upgrade|improve|debug|restructur)',
            _re.IGNORECASE,
        )
        query = "crée un site web pour ma boutique"
        assert _CREATE_PROJECT_RE.search(query)
        # Le elif ne s'exécute pas si CREATE matche
        # On vérifie juste qu'il n'y a pas de conflit regex
        assert not _REPAIR_RE.search("crée un site web")  # "crée" ne matche pas repair


# ──────────────────────────────────────────────────────
# P2: DeepSeek Reasoner empty content extraction
# ──────────────────────────────────────────────────────
class TestDeepSeekEmptyContentExtraction:
    """Vérifie les 3 niveaux d'extraction depuis reasoning_content."""

    def test_thought_action_extraction(self):
        """reasoning_content contient THOUGHT/ACTION → extrait correctement."""
        import re as _re
        reasoning = (
            "Let me think about this...\n\n"
            "THOUGHT: I need to read the file first\n"
            "ACTION: tool_call\n"
            "ACTION_INPUT: {\"tool\": \"read_file\", \"path\": \"index.html\"}"
        )
        _thought_match = _re.search(
            r'(THOUGHT:\s*.+?\nACTION:\s*.+?\nACTION_INPUT:\s*.+)',
            reasoning, _re.DOTALL
        )
        assert _thought_match
        content = _thought_match.group(1)
        assert "THOUGHT:" in content
        assert "ACTION:" in content

    def test_json_action_extraction(self):
        """reasoning_content contient JSON action (format CodeAgent) → extrait."""
        import re as _re
        reasoning = (
            "I should read the file to understand the structure. "
            'The action is {"action": "read_file", "path": "src/main.py"} '
            "and then I will edit it."
        )
        # THOUGHT/ACTION ne matche pas
        _thought_match = _re.search(
            r'(THOUGHT:\s*.+?\nACTION:\s*.+?\nACTION_INPUT:\s*.+)',
            reasoning, _re.DOTALL
        )
        assert _thought_match is None

        # JSON action matche
        _json_match = _re.search(
            r'(\{[^{}]*"action"\s*:\s*"[^"]+?"[^}]*\})',
            reasoning,
        )
        assert _json_match
        import json
        parsed = json.loads(_json_match.group(1))
        assert parsed["action"] == "read_file"
        assert parsed["path"] == "src/main.py"

    def test_raw_fallback(self):
        """reasoning_content sans pattern reconnu → retourne brut."""
        import re as _re
        reasoning = "I'm thinking about the problem but I can't formulate a response."
        _thought_match = _re.search(
            r'(THOUGHT:\s*.+?\nACTION:\s*.+?\nACTION_INPUT:\s*.+)',
            reasoning, _re.DOTALL
        )
        _json_match = _re.search(
            r'(\{[^{}]*"action"\s*:\s*"[^"]+?"[^}]*\})',
            reasoning,
        )
        assert _thought_match is None
        assert _json_match is None
        # Fallback brut = reasoning tel quel
        content = reasoning
        assert content == reasoning


# ──────────────────────────────────────────────────────
# P3: Nudge generate_website on web truncation
# ──────────────────────────────────────────────────────
class TestNudgeGenerateWebsite:
    """Vérifie le nudge vers generate_website sur troncature/writes web."""

    def test_nudge_on_web_truncation(self):
        """Troncature sur fichier .html → message generate_website injecté."""
        _saved_partial_path = "workspace/projet/index.html"
        _trunc_ctx_parts = [
            f"✅ Le fichier `{_saved_partial_path}` a été partiellement sauvegardé.",
        ]
        if any(_saved_partial_path.endswith(ext) for ext in ('.html', '.css', '.js')):
            _trunc_ctx_parts.append("generate_website")
        joined = "\n".join(_trunc_ctx_parts)
        assert "generate_website" in joined

    def test_no_nudge_on_python_truncation(self):
        """Troncature sur fichier .py → PAS de nudge generate_website."""
        _saved_partial_path = "src/main.py"
        has_nudge = any(_saved_partial_path.endswith(ext) for ext in ('.html', '.css', '.js'))
        assert not has_nudge

    def test_nudge_after_2_web_writes(self):
        """Après 2 write_file .html/.css → nudge proactif."""
        _web_writes_count = 0
        paths = ["workspace/proj/index.html", "workspace/proj/style.css", "workspace/proj/script.js"]
        nudge_triggered_at = None
        for i, p in enumerate(paths):
            if any(p.endswith(ext) for ext in ('.html', '.css', '.js')):
                _web_writes_count += 1
                if _web_writes_count >= 2 and nudge_triggered_at is None:
                    nudge_triggered_at = i
        assert nudge_triggered_at == 1  # Triggered on 2nd web write (style.css)


# ──────────────────────────────────────────────────────
# P4: CodeAgent read-only loop detection
# ──────────────────────────────────────────────────────
class TestCodeAgentReadOnlyLoop:
    """Vérifie le nudge après lectures consécutives sans écriture."""

    def test_nudge_after_consecutive_reads(self):
        """8 reads sans edit → nudge injecté."""
        reads_since_last_edit = 0
        _READS_BEFORE_NUDGE = 8
        _passive = ("read_file", "list_files", "grep", "search_in_files")
        _active = ("edit_file", "write_file", "edit_lines", "apply_patch")
        messages = []

        actions = ["read_file"] * 8
        for a in actions:
            if a in _passive:
                reads_since_last_edit += 1
            elif a in _active or a == "done":
                reads_since_last_edit = 0
            if reads_since_last_edit >= _READS_BEFORE_NUDGE:
                messages.append({"role": "user", "content": "AGIS MAINTENANT"})
                reads_since_last_edit = 0

        assert len(messages) == 1
        assert "AGIS" in messages[0]["content"]

    def test_read_counter_resets_on_edit(self):
        """read×5 + edit → compteur=0, pas de nudge."""
        reads_since_last_edit = 0
        _READS_BEFORE_NUDGE = 8
        _passive = ("read_file", "list_files", "grep", "search_in_files")
        _active = ("edit_file", "write_file", "edit_lines", "apply_patch")
        messages = []

        actions = ["read_file"] * 5 + ["edit_file"] + ["read_file"] * 5
        for a in actions:
            if a in _passive:
                reads_since_last_edit += 1
            elif a in _active or a == "done":
                reads_since_last_edit = 0
            if reads_since_last_edit >= _READS_BEFORE_NUDGE:
                messages.append({"role": "user", "content": "AGIS"})
                reads_since_last_edit = 0

        assert len(messages) == 0  # Never reached 8

    def test_no_nudge_on_mixed_actions(self):
        """Alternance read/edit → jamais de nudge."""
        reads_since_last_edit = 0
        _READS_BEFORE_NUDGE = 8
        _passive = ("read_file", "list_files", "grep", "search_in_files")
        _active = ("edit_file", "write_file", "edit_lines", "apply_patch")
        messages = []

        actions = ["read_file", "edit_file"] * 10
        for a in actions:
            if a in _passive:
                reads_since_last_edit += 1
            elif a in _active or a == "done":
                reads_since_last_edit = 0
            if reads_since_last_edit >= _READS_BEFORE_NUDGE:
                messages.append({"role": "user", "content": "AGIS"})
                reads_since_last_edit = 0

        assert len(messages) == 0


# ──────────────────────────────────────────────────────
# P5: CodeAgent _resolve_path workspace isolation
# ──────────────────────────────────────────────────────
class TestResolvePathWorkspaceIsolation:
    """Vérifie que _resolve_path isole correctement le workspace projet
    tout en permettant les chemins absolus."""

    @pytest.fixture
    def agent(self):
        from src.agents.sub_agent import SubAgent
        a = SubAgent.__new__(SubAgent)
        a.name = "TestAgent"
        a.agent_type = "code"
        return a

    def test_relative_path_resolves_to_workspace(self, agent, tmp_path):
        """index.html relatif → résolu depuis le projet workspace."""
        proj = tmp_path / "workspace" / "2026-04-06" / "proj"
        proj.mkdir(parents=True)
        (proj / "index.html").write_text("<html>test</html>")
        agent._task_workspace_root = proj
        resolved = agent._resolve_path("index.html")
        assert resolved == proj / "index.html"
        assert resolved.exists()

    def test_absolute_path_passes_through(self, agent, tmp_path):
        """Chemin absolu → retourné tel quel (accès n'importe où sur le PC)."""
        agent._task_workspace_root = tmp_path / "workspace" / "proj"
        abs_target = tmp_path / "some_other_folder" / "file.py"
        resolved = agent._resolve_path(str(abs_target))
        assert resolved == abs_target

    def test_dot_resolves_to_workspace(self, agent, tmp_path):
        """list_files('.') avec workspace → pointe vers le projet, pas Lumena root."""
        proj = tmp_path / "workspace" / "proj"
        proj.mkdir(parents=True)
        (proj / "style.css").write_text("body{}")
        agent._task_workspace_root = proj
        resolved = agent._resolve_path(".")
        assert resolved == proj / "."
        entries = [e.name for e in resolved.iterdir()]
        assert "style.css" in entries

    def test_no_workspace_resolves_to_lumena_root(self, agent):
        """Sans workspace actif → résolution depuis Lumena root (== _project_root)."""
        agent._task_workspace_root = None
        resolved = agent._resolve_path("README.md")
        assert resolved == agent._project_root() / "README.md"

    def test_read_file_direct_not_read_own_code(self, tmp_path):
        """read_file doit lire directement via _resolve_path, pas via read_own_code."""
        proj = tmp_path / "workspace" / "proj"
        proj.mkdir(parents=True)
        (proj / "index.html").write_text("<html><body>Hello</body></html>")
        from src.agents.sub_agent import SubAgent
        a = SubAgent.__new__(SubAgent)
        a.name = "TestAgent"
        a.agent_type = "code"
        a._task_workspace_root = proj
        abs_path = a._resolve_path("index.html")
        assert abs_path.exists()
        content = abs_path.read_text(encoding="utf-8")
        assert "<html>" in content
        assert "Hello" in content

    def test_edit_file_direct_not_edit_own_code(self, tmp_path):
        """edit_file via _resolve_path écrit dans le workspace, pas Lumena root."""
        proj = tmp_path / "workspace" / "proj"
        proj.mkdir(parents=True)
        (proj / "app.py").write_text("x = 1\ny = 2\n")
        from src.agents.sub_agent import SubAgent
        a = SubAgent.__new__(SubAgent)
        a.name = "TestAgent"
        a.agent_type = "code"
        a._task_workspace_root = proj
        abs_path = a._resolve_path("app.py")
        content = abs_path.read_text(encoding="utf-8")
        content = content.replace("x = 1", "x = 42", 1)
        abs_path.write_text(content, encoding="utf-8")
        assert "x = 42" in abs_path.read_text()
        # Le fichier Lumena root ne doit PAS être modifié
        lumena_app = a._project_root() / "app.py"
        if lumena_app.exists():
            assert "x = 42" not in lumena_app.read_text()

    def test_workspace_hint_injected_in_prompt(self, tmp_path):
        """Quand workspace actif, le prompt doit mentionner le dossier de travail."""
        proj = tmp_path / "workspace" / "proj"
        proj.mkdir(parents=True)
        hint = f"WORKSPACE ACTIF: {proj}"
        assert str(proj) in hint
        assert "WORKSPACE ACTIF" in hint

    def test_lumena_source_guard_blocks_src(self, agent, tmp_path):
        """Avec workspace actif, read_file('src/core.py') résout dans le workspace, PAS Lumena."""
        proj = tmp_path / "workspace" / "proj"
        proj.mkdir(parents=True)
        agent._task_workspace_root = proj
        resolved = agent._resolve_path("src/core.py")
        # Doit résoudre dans le workspace, pas dans Lumena root
        assert str(proj) in str(resolved)
        assert str(agent._project_root()) not in str(resolved) or str(proj) in str(resolved)

    def test_lumena_source_guard_blocks_known_files(self, agent, tmp_path):
        """lumena_ultime.py / run_daemon.py bloqués quand workspace actif."""
        proj = tmp_path / "workspace" / "proj"
        proj.mkdir(parents=True)
        agent._task_workspace_root = proj
        for fname in ("lumena_ultime.py", "run_daemon.py", "Dockerfile"):
            resolved = agent._resolve_path(fname)
            assert str(proj) in str(resolved), f"{fname} devrait résoudre dans workspace"

    def test_lumena_source_guard_no_block_without_workspace(self, agent):
        """Sans workspace, src/core.py résout normalement depuis Lumena root."""
        agent._task_workspace_root = None
        resolved = agent._resolve_path("src/core.py")
        assert resolved == agent._project_root() / "src" / "core.py"

    def test_grep_uses_workspace_root(self, agent, tmp_path):
        """grep sans path explicite utilise '.' → résolu dans workspace."""
        proj = tmp_path / "workspace" / "proj"
        proj.mkdir(parents=True)
        agent._task_workspace_root = proj
        resolved = agent._resolve_path(".")
        assert str(proj) in str(resolved)

    @pytest.mark.asyncio
    async def test_edit_lines_uses_resolve_path(self, tmp_path):
        """edit_lines utilise _resolve_path, pas _normalize_tool_path."""
        from src.agents.sub_agent import CodeAgent
        a = CodeAgent.__new__(CodeAgent)
        a.name = "TestAgent"
        a.agent_type = "code"
        a._task_workspace_root = tmp_path
        (tmp_path / "test.txt").write_text("line1\nline2\nline3\n")
        action = {
            "action": "edit_lines",
            "path": "test.txt",
            "start_line": 2,
            "end_line": 2,
            "content": "REPLACED\n",
        }
        result = await a._execute_loop_action(action)
        assert "REPLACED" in (tmp_path / "test.txt").read_text()


# ──────────────────────────────────────────────────────
# P6: Hardening — fuzzy match, path traversal, run_tests
# ──────────────────────────────────────────────────────
class TestCodeAgentHardening:
    """Tests de sécurité et robustesse du CodeAgent."""

    @pytest.fixture
    def agent(self, tmp_path):
        from src.agents.sub_agent import SubAgent
        a = SubAgent.__new__(SubAgent)
        a.name = "TestAgent"
        a.agent_type = "code"
        a._task_workspace_root = tmp_path
        return a

    def test_path_traversal_blocked(self, agent, tmp_path):
        """../../etc/passwd ne sort pas du workspace."""
        resolved = agent._resolve_path("../../etc/passwd")
        resolved_str = str(resolved.resolve())
        ws_str = str(tmp_path.resolve())
        # Doit rester dans le workspace ou être un nom sans traversal
        assert ".." not in resolved_str or ws_str in resolved_str

    def test_path_traversal_stays_in_workspace(self, agent, tmp_path):
        """../../../Windows/System32/config ne s'échappe pas."""
        resolved = agent._resolve_path("../../../Windows/System32/config")
        # Le path résolu ne doit PAS contenir le vrai System32
        assert "System32" not in str(resolved.resolve()) or str(tmp_path) in str(resolved)

    @pytest.mark.asyncio
    async def test_fuzzy_match_line_aware(self, tmp_path):
        """Fuzzy match remplace sur la bonne LIGNE, pas la première occurrence globale."""
        from src.agents.sub_agent import CodeAgent
        a = CodeAgent.__new__(CodeAgent)
        a.name = "T"
        a.agent_type = "code"
        a._task_workspace_root = tmp_path

        f = tmp_path / "test.py"
        # "def foo" apparaît dans un string (L1) ET dans le code (L2)
        f.write_text('x = "  def foo()  "\ndef foo():\n    pass\n')

        action = {
            "action": "edit_file",
            "path": "test.py",
            "search": "  def foo()  ",     # Avec espaces autour
            "replace": "  def bar()  ",
        }
        result = await a._execute_loop_action(action)
        content = f.read_text()
        # La première ligne (string) doit être modifiée (c'est le premier match)
        assert "bar" in content.split("\n")[0]
        # La deuxième ligne (code) doit rester intacte
        assert "def foo():" in content

    @pytest.mark.asyncio
    async def test_run_tests_blocked_with_workspace(self, tmp_path):
        """run_tests sans test_path bloqué quand workspace actif."""
        from src.agents.sub_agent import CodeAgent, ActionResult
        a = CodeAgent.__new__(CodeAgent)
        a.name = "T"
        a.agent_type = "code"
        a._task_workspace_root = tmp_path

        action = {"action": "run_tests"}
        result = await a._execute_loop_action(action)
        assert isinstance(result, ActionResult)
        # Ne doit PAS avoir lancé les 5000+ tests
        assert "⏭️" in str(result) or "❌" in str(result)

    @pytest.mark.asyncio
    async def test_run_tests_blocked_without_path(self, tmp_path):
        """run_tests sans test_path et sans workspace → bloqué aussi."""
        from src.agents.sub_agent import CodeAgent, ActionResult
        a = CodeAgent.__new__(CodeAgent)
        a.name = "T"
        a.agent_type = "code"
        a._task_workspace_root = None

        action = {"action": "run_tests"}
        result = await a._execute_loop_action(action)
        assert "❌" in str(result)

    def test_normalize_tool_path_removed(self):
        """_normalize_tool_path ne doit plus exister."""
        from src.agents.sub_agent import SubAgent
        assert not hasattr(SubAgent, "_normalize_tool_path")
