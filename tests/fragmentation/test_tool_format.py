"""
Tests de qualité des outils (Phase 5.1 — PLAN_TOOL_QUALITY).

Vérifie que TOUS les HandlerDefs respectent le format uniforme :
  1. description non vide
  2. parameters en JSON Schema (clé "properties" obligatoire)
  3. clé "required" présente dans parameters
  4. chaque param dans properties a "type" et "description"
  5. aucun doublon de nom entre modules
  6. les outils critiques ont des descriptions de paramètres

Ce fichier sert de garde-fou : toute régression sur la qualité outil
sera détectée immédiatement.
"""

import pytest
from collections import Counter
from typing import List

from src.reasoning.handlers.registry_v2 import HandlerDef

# ─── Import de tous les modules handlers ─────────────────────────────────

from src.reasoning.handlers.browser import get_browser_handler_defs
from src.reasoning.handlers.computer_use import get_computer_use_handler_defs
from src.reasoning.handlers.skills import get_skills_handler_defs
from src.reasoning.handlers.agents import get_agents_handler_defs
from src.reasoning.handlers.mail import get_mail_handler_defs
from src.reasoning.handlers.documents import get_documents_handler_defs
from src.reasoning.handlers.spotify import get_spotify_handler_defs
from src.reasoning.handlers.notion import get_notion_handler_defs
from src.reasoning.handlers.files import get_file_handler_defs
from src.reasoning.handlers.system import get_system_handler_defs
from src.reasoning.handlers.web import get_web_handler_defs
from src.reasoning.handlers.memory import get_memory_handler_defs
from src.reasoning.handlers.ionos import get_ionos_handler_defs


# ─── Collecte de tous les HandlerDefs ────────────────────────────────────

ALL_GETTER_FUNCS = [
    ("browser", get_browser_handler_defs),
    ("computer_use", get_computer_use_handler_defs),
    ("skills", get_skills_handler_defs),
    ("agents", get_agents_handler_defs),
    ("mail", get_mail_handler_defs),
    ("documents", get_documents_handler_defs),
    ("spotify", get_spotify_handler_defs),
    ("notion", get_notion_handler_defs),
    ("files", get_file_handler_defs),
    ("system", get_system_handler_defs),
    ("web", get_web_handler_defs),
    ("memory", get_memory_handler_defs),
    ("ionos", get_ionos_handler_defs),
]


def _all_handler_defs() -> List[HandlerDef]:
    """Retourne la liste aplatie de tous les HandlerDefs."""
    result = []
    for _module_name, getter in ALL_GETTER_FUNCS:
        result.extend(getter())
    return result


def _all_handler_defs_with_module() -> list:
    """Retourne [(module_name, HandlerDef), ...]."""
    result = []
    for module_name, getter in ALL_GETTER_FUNCS:
        for hdef in getter():
            result.append((module_name, hdef))
    return result


ALL_HDEFS = _all_handler_defs()
ALL_HDEFS_WITH_MODULE = _all_handler_defs_with_module()

# IDs pour pytest parametrize
ALL_IDS = [hd.name for hd in ALL_HDEFS]
ALL_IDS_WITH_MODULE = [f"{m}::{hd.name}" for m, hd in ALL_HDEFS_WITH_MODULE]


# ═══════════════════════════════════════════════════════════════════════════
# 1. Chaque HandlerDef a une description non vide
# ═══════════════════════════════════════════════════════════════════════════

class TestDescriptions:
    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_description_non_empty(self, hdef: HandlerDef):
        assert hdef.description, f"{hdef.name}: description vide"

    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_description_is_string(self, hdef: HandlerDef):
        assert isinstance(hdef.description, str), f"{hdef.name}: description n'est pas str"

    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_description_min_length(self, hdef: HandlerDef):
        assert len(hdef.description) >= 5, (
            f"{hdef.name}: description trop courte ({len(hdef.description)} chars)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 2. JSON Schema : clé "properties" obligatoire
# ═══════════════════════════════════════════════════════════════════════════

class TestJSONSchemaFormat:
    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_has_properties_key(self, hdef: HandlerDef):
        """Tous les parameters doivent être en JSON Schema (clé 'properties')."""
        params = hdef.parameters
        assert "properties" in params, (
            f"{hdef.name}: parameters n'a pas de clé 'properties' — "
            f"encore en format FLAT ? Clés trouvées : {list(params.keys())}"
        )

    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_has_required_key(self, hdef: HandlerDef):
        """Tous les parameters doivent avoir une clé 'required' (liste, même vide)."""
        params = hdef.parameters
        assert "required" in params, (
            f"{hdef.name}: parameters n'a pas de clé 'required'"
        )

    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_required_is_list(self, hdef: HandlerDef):
        """'required' doit être une liste."""
        req = hdef.parameters.get("required", None)
        if req is not None:
            assert isinstance(req, list), (
                f"{hdef.name}: 'required' n'est pas une liste, type={type(req).__name__}"
            )

    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_properties_is_dict(self, hdef: HandlerDef):
        """'properties' doit être un dict."""
        props = hdef.parameters.get("properties", None)
        if props is not None:
            assert isinstance(props, dict), (
                f"{hdef.name}: 'properties' n'est pas un dict"
            )

    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_required_fields_exist_in_properties(self, hdef: HandlerDef):
        """Tous les champs listés dans 'required' doivent exister dans 'properties'."""
        params = hdef.parameters
        props = params.get("properties", {})
        required = params.get("required", [])
        for field in required:
            assert field in props, (
                f"{hdef.name}: champ requis '{field}' absent de properties"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 3. Chaque paramètre a "type" et "description"
# ═══════════════════════════════════════════════════════════════════════════

class TestParamQuality:
    @pytest.mark.parametrize(
        "module_name,hdef", ALL_HDEFS_WITH_MODULE, ids=ALL_IDS_WITH_MODULE
    )
    def test_each_param_has_type(self, module_name: str, hdef: HandlerDef):
        """Chaque param dans properties doit avoir un 'type'."""
        props = hdef.parameters.get("properties", {})
        for param_name, param_info in props.items():
            assert "type" in param_info, (
                f"{module_name}::{hdef.name}.{param_name}: manque 'type'"
            )

    @pytest.mark.parametrize(
        "module_name,hdef", ALL_HDEFS_WITH_MODULE, ids=ALL_IDS_WITH_MODULE
    )
    def test_each_param_has_description(self, module_name: str, hdef: HandlerDef):
        """Chaque param dans properties doit avoir une 'description'."""
        props = hdef.parameters.get("properties", {})
        for param_name, param_info in props.items():
            assert "description" in param_info, (
                f"{module_name}::{hdef.name}.{param_name}: manque 'description'"
            )

    @pytest.mark.parametrize(
        "module_name,hdef", ALL_HDEFS_WITH_MODULE, ids=ALL_IDS_WITH_MODULE
    )
    def test_param_type_is_valid(self, module_name: str, hdef: HandlerDef):
        """Les types doivent être des types JSON Schema valides."""
        valid_types = {"string", "integer", "number", "boolean", "array", "object"}
        props = hdef.parameters.get("properties", {})
        for param_name, param_info in props.items():
            ptype = param_info.get("type", "")
            assert ptype in valid_types, (
                f"{module_name}::{hdef.name}.{param_name}: "
                f"type '{ptype}' invalide (valides: {valid_types})"
            )


# ═══════════════════════════════════════════════════════════════════════════
# 4. Pas de doublons de noms entre modules
# ═══════════════════════════════════════════════════════════════════════════

class TestNoDuplicates:
    def test_no_duplicate_names_across_modules(self):
        """Aucun nom d'outil ne doit apparaître dans plus d'un module."""
        name_to_modules = {}
        for module_name, hdef in ALL_HDEFS_WITH_MODULE:
            name_to_modules.setdefault(hdef.name, []).append(module_name)

        duplicates = {
            name: modules
            for name, modules in name_to_modules.items()
            if len(modules) > 1
        }
        assert not duplicates, (
            f"Noms en doublon entre modules : {duplicates}"
        )

    def test_total_handler_count(self):
        """
        Nombre total de handlers attendu : 250.
        Si ce nombre change, il faut mettre à jour ce test
        (et comprendre pourquoi).
        +1 (Étape 2 IONOS) : ionos_test_site_database.
        +2 (Étape 2.5 IONOS) : ionos_set_site_database, ionos_clear_site_database.
        +3 (Étape 3E IONOS) : ionos_db_list_tables, ionos_db_describe_table, ionos_db_select.
        +1 (Étape 4.5A IONOS) : ionos_db_propose_write (propose-only INSERT/UPDATE, pas d'exécution).
        +1 (Étape 4.5B IONOS) : ionos_db_propose_delete (propose-only DELETE, OFF par défaut).
        +18 (Exposition ReAct IONOS) : get_config, bridge_status, get/set des configs
            write/delete/sandbox/restore/react_write/react_delete, list_snapshots,
            list_pending_actions, install_bridge, create_sandbox_table.
        +3 (Étape 4.6 IONOS) : get/set sandbox_drop_config, propose_drop_sandbox_table.
        +3 (Étape 4.7 IONOS) : get/set sandbox_clear_config, propose_clear_sandbox_table.
        """
        assert len(ALL_HDEFS) == 287, (
            f"Attendu 287 handlers, trouvé {len(ALL_HDEFS)}. "
            f"Mettre à jour ce test si ajout/suppression intentionnel."
        )

    def test_twelve_modules(self):
        """Il doit y avoir exactement 13 modules de handlers."""
        assert len(ALL_GETTER_FUNCS) == 13


# ═══════════════════════════════════════════════════════════════════════════
# 5. Outils critiques — vérifications spécifiques
# ═══════════════════════════════════════════════════════════════════════════

# Outils critiques qui DOIVENT exister et avoir des params bien documentés
CRITICAL_TOOLS = [
    "read_file",
    "write_file",
    "web_search",
    "screenshot",
    "parallel_tools",
    "browser_navigate",
    "mail_send",
    "delegate_task",
    "read_own_code",
    "create_skill",
]


class TestCriticalTools:
    def test_all_critical_tools_exist(self):
        """Les outils critiques doivent tous être définis."""
        all_names = {hd.name for hd in ALL_HDEFS}
        missing = [t for t in CRITICAL_TOOLS if t not in all_names]
        assert not missing, f"Outils critiques manquants : {missing}"

    @pytest.mark.parametrize("tool_name", CRITICAL_TOOLS)
    def test_critical_tools_have_params_documented(self, tool_name: str):
        """
        Les outils critiques avec des paramètres doivent avoir
        au moins un paramètre avec description.
        """
        hdef = next((h for h in ALL_HDEFS if h.name == tool_name), None)
        if hdef is None:
            pytest.skip(f"{tool_name} non trouvé")
        props = hdef.parameters.get("properties", {})
        if not props:
            # Outil sans paramètre (ex: screenshot) — OK
            return
        # Au moins un param doit avoir une description non vide
        has_desc = any(
            p.get("description", "").strip()
            for p in props.values()
        )
        assert has_desc, (
            f"{tool_name}: aucun paramètre n'a de description"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 6. screenshot ne doit être défini qu'une seule fois (dans system.py)
# ═══════════════════════════════════════════════════════════════════════════

class TestScreenshotUnicity:
    def test_screenshot_defined_once(self):
        """screenshot ne doit exister que dans system.py (pas de doublon)."""
        screenshot_modules = [
            mod for mod, hd in ALL_HDEFS_WITH_MODULE
            if hd.name == "screenshot"
        ]
        assert len(screenshot_modules) == 1, (
            f"screenshot défini dans {len(screenshot_modules)} modules : "
            f"{screenshot_modules} (attendu: system uniquement)"
        )
        assert screenshot_modules[0] == "system", (
            f"screenshot défini dans '{screenshot_modules[0]}', attendu 'system'"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 7. Cohérence nom / handler callable
# ═══════════════════════════════════════════════════════════════════════════

class TestHandlerCallable:
    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_handler_is_callable(self, hdef: HandlerDef):
        """Le handler doit être un callable."""
        assert callable(hdef.handler), (
            f"{hdef.name}: handler n'est pas callable"
        )

    @pytest.mark.parametrize("hdef", ALL_HDEFS, ids=ALL_IDS)
    def test_name_is_valid_identifier(self, hdef: HandlerDef):
        """Le nom de l'outil doit être un identifiant Python valide (snake_case)."""
        assert hdef.name.replace("_", "").isalnum(), (
            f"'{hdef.name}': nom invalide (doit être alphanumérique + underscores)"
        )
        assert not hdef.name.startswith("_"), (
            f"'{hdef.name}': ne doit pas commencer par underscore"
        )
