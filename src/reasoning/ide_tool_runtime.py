"""IDE catalogue projection and strict dispatch, outside the ReAct orchestrator."""
from __future__ import annotations

import json
import uuid

from loguru import logger

from .external_tool_registry import ExternalProviderSnapshot, ExternalToolCatalog, ExternalToolError
from .external_tool_scope import oublier_catalogue_externe, run_external_catalog
from .ide_error_guidance import expliquer_erreur_ide
from .ide_mission_scope import (
    MissionScopeError, authorize_mission_call, canonical_workspace, derive_mission_scope,
    is_mission_content_write, mission_envelope_scope, mission_write_guard, prepare_mission_write,
    verify_mission_write,
)
from .ide_mission_execution import (
    follow_mission_execution, is_mission_execution, listing_action, mission_execution_envelope,
    prepare_mission_command, prepare_mission_execution,
)
from ..tools.ide_protocol import MISSION_SCOPE_PROTOCOL_VERSION
from .react_config import Observation
from .tool_semantics import Confirmation, ProviderKind, ToolEffect
from .tool_result import ide_execution_result
from .external_effect_cache import external_effect_cache
from .external_execution_trace import trace_ide_attempt
from ..runtime.context import get_current_runtime_context
from ..runtime.permissions import is_owner
from ..utils.external_tool_names import is_ide_tool_name


def _required_parameters(prepared) -> frozenset:
    try:
        return frozenset(json.loads(prepared.spec.schema_json).get("required") or ())
    except (TypeError, ValueError, AttributeError):
        return frozenset()


class RegistryIDEProvider:
    def __init__(self, registry) -> None:
        self.registry = registry
        self._service = None
        self._handlers = {}

    @property
    def service(self):
        if self._service is None:
            from ..tools.ide_bridge import get_ide_bridge
            from ..tools.ide_capabilities import IDECapabilityService
            from ..tools.ide_discovery import IDEDiscoveryService

            self._service = IDECapabilityService(IDEDiscoveryService(self.registry.lumena_root), get_ide_bridge())
        return self._service

    def _capture(self, workspace=None) -> ExternalToolCatalog:
        try:
            snapshot = self.service.capture(workspace=workspace)
            if type(snapshot) is not ExternalProviderSnapshot or snapshot.provider_id != "lumena.ide":
                raise ExternalToolError("external_provider_identity_changed")
            if any(not is_ide_tool_name(tool.name) for tool in snapshot.tools):
                raise ExternalToolError("external_native_collision")
            return ExternalToolCatalog((snapshot,))
        except Exception as exc:
            # Discovery failure cannot resurrect the old unscoped facades.
            logger.warning("IDE catalogue unavailable ({})", type(exc).__name__)
            return ExternalToolCatalog((ExternalProviderSnapshot("lumena.ide", "degraded", ()),))

    def catalog(self, workspace=None) -> ExternalToolCatalog:
        """LOT L5-3c-4 : `workspace` ancre le catalogue sur l'instance d'une mission.

        La cle de cache le porte : sans cela, le catalogue de la PROPRIETAIRE, deja
        capture pour ce tour, serait rendu a la mission - c'est-a-dire la fenetre de
        l'utilisateur. Sans `workspace`, la cle est l'historique, a l'identique.
        """
        return run_external_catalog(self._cle_de_catalogue(workspace),
                                    lambda: self._capture(workspace))

    def _cle_de_catalogue(self, workspace=None):
        """Une seule definition : `catalog` et l'oubli de L5-4a doivent coincider."""
        if workspace is None:
            return (str(self.registry.lumena_root), "lumena.ide")
        return (str(self.registry.lumena_root), "lumena.ide", str(workspace))

    def entries(self) -> dict:
        entries = {}
        for provider in self.catalog().providers:
            for spec in provider.tools:
                if spec.name not in self._handlers:
                    async def handler(_name=spec.name, **parameters):
                        return await self.registry.execute(_name, parameters)
                    self._handlers[spec.name] = handler
                schema = spec.api_schema()["function"]["parameters"]
                entries[spec.name] = {
                    "name": spec.name, "description": spec.description,
                    "parameters": schema.get("properties", {}), "required": schema.get("required", []),
                    "input_schema": schema, "handler": self._handlers[spec.name],
                }
        return entries

    def modules(self) -> dict:
        return {tool.name: "ide" for provider in self.catalog().providers for tool in provider.tools}

    async def _ouvrir_instance_de_mission(self, scope) -> None:
        """LOT L5-3b : une mission obtient SA fenetre, jamais celle de l'utilisateur.

        Ouverture PARESSEUSE : appelee seulement quand l'IDE disponible n'est pas
        ouverte sur le `mission_root`. Une mission qui n'utilise aucun outil `ide__*`
        ne paie rien.

        Le profil est isole par `user_data_dir` (lot L5-3) : sans lui, Electron
        refuse la seconde instance, son verrou d'instance unique etant lie au
        repertoire `userData`. L'emplacement suit la convention de
        `BROWSER_PROFILES_DIR` : un sous-dossier par identite.

        Import LOCAL du lanceur, comme `handlers/computer_use.py` : la substitution
        reste possible en test et aucune dependance de module n'est ancree.

        Best-effort : si aucune instance ne peut s'ouvrir, `authorize_mission_call`
        refusera ensuite exactement comme avant. La mission echoue proprement — elle
        ne se rabat JAMAIS sur la fenetre de l'utilisateur (regle du lot L5-2).
        """
        from ..tools.ide_launcher import get_ide_launcher
        from ..utils.paths import DATA_DIR

        profil = DATA_DIR / "ide_profiles" / scope.task_id
        ouverte = False
        try:
            # `dedicated=True` (L5-3b-bis) : sans lui, `ensure_ready` se contente de
            # ROUTER l'IDE deja connectee, donc de deplacer la fenetre de
            # l'utilisateur - exactement ce que L5-2 interdit.
            resultat = await get_ide_launcher().ensure_ready(
                scope.mission_root, user_data_dir=profil, dedicated=True,
            )
            ouverte = bool(getattr(resultat, "available", False))
        except Exception as exc:
            logger.debug("[L5-3b] instance de mission indisponible: {}", type(exc).__name__)
        if ouverte:
            # LOT L5-4a : le catalogue de ce dossier a ete capture AVANT l'ouverture,
            # donc comme `launch_only`. Sans cet oubli, le rail relit ce cache et
            # refuse par `ide_mission_workspace_mismatch` alors que l'instance est
            # vivante - defaut mesure au canari reel, invisible en test unitaire.
            oublier_catalogue_externe(self._cle_de_catalogue(scope.mission_root))

    def _internal_dispatch(self, session):
        """Suivi `operation_get` / `operation_cancel` INTERNE, comme la sonde 4B :
        jamais promu dans le catalogue du modele."""
        from .external_tool_registry import ExternalToolSpec, PreparedExternalCall
        from .tool_semantics import Availability, validate_semantic_announcement
        from ..tools.ide_semantics import local_ide_semantics

        async def dispatch(action, params):
            descriptor = next((item for item in session.commands if item["id"] == action), None)
            if descriptor is None:
                raise ExternalToolError("ide_followup_unavailable")
            semantics = validate_semantic_announcement(local_ide_semantics(
                action, instance_id=session.instance_id, revision=session.catalogue_hash,
                availability=Availability.READY), descriptor["semantics"])
            spec = ExternalToolSpec(semantics, descriptor["description"], json.dumps(descriptor["input_schema"]))
            prepared = PreparedExternalCall(spec, semantics, json.dumps(params))
            raw = await self.service.bridge.send_command(action, prepared.parameters, expected_snapshot=session)
            if type(raw) is not dict:
                raise ExternalToolError("ide_result_invalid")
            record = (ide_execution_result(prepared, raw, session, json.dumps(raw, ensure_ascii=False))
                      if action == "operation_get" else None)
            return raw, record
        return dispatch

    def _vue_de_la_mission(self, scope, name, args, snapshot, prepared):
        """Le snapshot ancre sur le dossier de la mission, ou celui deja en main.

        LOT L5-3c-4 : `prepared` est RECONSTRUIT contre le snapshot retenu. Il porte
        le schema et les parametres valides ; le garder lie a une autre generation
        ferait juger un appel sur la description d'une autre instance.

        Best-effort assume : si le catalogue de la mission n'existe pas encore
        (instance pas encore connectee), on rend la vue courante et l'autorisation
        refuse ensuite exactement comme avant. Jamais de retombee silencieuse.
        """
        try:
            vue, _ = self.catalog(workspace=scope.mission_root).resolve(name)
            return vue, self.service.prepare(vue, name, args)
        except Exception as exc:
            logger.debug("[L5-3c] catalogue de mission indisponible: {}", type(exc).__name__)
            return snapshot, prepared

    async def _executer_cycle_de_vie(self, name, args, prepared, attempt) -> Observation:
        """LOT CONN-7d — lancer l'IDE, localement, par son handler natif.

        Le catalogue expose `ide_launch` pour que le modele le VOIE, mais son
        execution n'a rien d'un envoi : elle appelle le lanceur. Le handler natif
        `_handle_ide_launch` porte deja cette logique, avec son attente de handshake
        et ses messages - on ne la reecrit pas ici.
        """
        from .handlers.files import PathSecurityError, assert_execution_cwd_allowed
        from .handlers.ide import _handle_ide_launch

        ctx = getattr(self.registry, "_v2_context", None)
        # GARDE 1 — le dossier demande est juge comme un dossier de travail.
        #
        # Rien ne le bornait : le schema ne declare qu'un « chemin du dossier a
        # ouvrir », et `_validate_workspace` verifie seulement qu'il EXISTE. Un
        # `ide_launch(workspace="C:/Windows/System32")` passait donc. On reutilise
        # le juge de L2-1, deja employe par les cinq points d'execution natifs -
        # pas une regle de plus, la meme.
        demande = (args or {}).get("workspace") or ""
        if demande:
            try:
                assert_execution_cwd_allowed(str(demande), ctx, outil="ide_launch")
            except PathSecurityError as refus:
                logger.warning("[CONN-7d] lancement refuse : workspace={}", str(demande)[:120])
                return Observation(content=f"IDE: {refus}", success=False,
                                   guidance=expliquer_erreur_ide("ide_launch_workspace_refuse"))
            except Exception:
                pass  # contexte leger : ne juge pas, ne plante pas (lecon L1c-1)

        # GARDE 2 — un lancement de processus s'enregistre au cache d'effets.
        #
        # Mon premier routage l'esquivait : le lancement d'une IDE n'etait trace
        # NULLE PART, alors que c'est un `PROCESS_LAUNCH`. C'est le mecanisme meme
        # que CONN-7b venait de reparer. Le ticket est referme dans tous les cas -
        # y compris a l'echec, ou l'IDE n'a jamais demarre : rien a prouver, donc
        # rien a suspendre (meme raisonnement que CONN-7b).
        billet = external_effect_cache().begin(prepared.semantics, None)
        try:
            resultat = await _handle_ide_launch(ctx, **(args or {}))
        finally:
            external_effect_cache().abandonner(billet)
        succes = bool(getattr(resultat, "success", False))
        trace_ide_attempt(attempt, name=prepared.spec.name, semantics=prepared.semantics,
                          reason="completed" if succes else "refused")
        # IDE-1a — `HandlerResult` porte son texte dans `output` (et `error` a l'echec) ;
        # il n'a PAS d'attribut `content`. Mon routage de CONN-7d lisait `content`, donc
        # il rendait TOUJOURS une observation VIDE.
        #
        # Mesure du run reel de Charles, 24/09 14:44:39 : `ide_launch` tourne 8 secondes,
        # lance reellement l'IDE — la fenetre apparait dans `list_windows` juste apres —
        # et l'observation arrive vide. Le modele n'a donc AUCUN moyen de savoir que son
        # lancement a reussi : il a enchaine cinq iterations de verification a l'aveugle,
        # screenshots et `list_windows`, pour deviner ce que l'outil savait deja.
        #
        # Un outil muet est pire qu'un outil qui echoue : l'echec, au moins, se lit.
        texte = ""
        try:
            texte = str(resultat.to_legacy_str() or "")
        except Exception:
            texte = str(getattr(resultat, "output", "") or getattr(resultat, "error", "") or "")
        if not texte.strip():
            # Dernier filet : ne JAMAIS rendre le vide. Dire l'issue, meme sans detail.
            texte = ("IDE: lancement effectue, sans detail rapporte par le lanceur."
                     if succes else "IDE: lancement echoue, sans detail rapporte par le lanceur.")
        return Observation(content=texte, success=succes,
                           guidance=None if succes else expliquer_erreur_ide("ide_launch_failed"))

    def _resoudre_frais(self, name: str, args: dict):
        """LOT IDE-6 : un catalogue PERIME n'est pas un catalogue stable.

        Run du 24/09 a 20 h 04 : **28 refus `ide_snapshot_stale` sur 32 iterations**,
        tous les outils `ide__*` murs, alors que le pont etait sain - `lumena_ide(status)`
        repondait `connected/handshake/authenticated` apres une instance neuve. Ce n'est
        pas l'IDE qui etait cassee, c'est la photo qui etait morte.

        Cause : `ExternalToolRun.capture` garde le catalogue d'une cle pour tout le tour,
        et `is_current` compare la session PAR IDENTITE. Des que `lumena_ide` change la
        session, le snapshot en cache est perime - et rien ne le jetait. L'invalidation
        existait depuis L5-4a, mais seulement pour les missions (l. ~142).

        On ne pose pas l'oubli dans `lumena_ide` : cela ne fermerait que la cause connue.
        On traite le fait - une photo perimee se reprend, **une seule fois**. CONN-3C
        tient : le catalogue reste stable tant qu'il est VALIDE. Si la seconde photo est
        encore perimee, l'IDE bouge vraiment sous nos pieds et le refus est legitime.

        L'ancrage ne change pas : meme cle, donc meme workspace. Les bornes de L5-2 et
        L5-3c restent entieres.
        """
        snapshot, _ = self.catalog().resolve(name)
        try:
            return snapshot, self.service.prepare(snapshot, name, args)
        except ExternalToolError as exc:
            # Seule la peremption se rattrape : tout autre motif garde sa vraie raison.
            if str(exc) != "ide_snapshot_stale":
                raise
        oublier_catalogue_externe(self._cle_de_catalogue())
        snapshot, _ = self.catalog().resolve(name)
        return snapshot, self.service.prepare(snapshot, name, args)

    async def execute(self, name: str, args: dict, *, caller=None) -> Observation:
        attempt = uuid.uuid4().hex
        prepared = None
        try:
            snapshot, prepared = self._resoudre_frais(name, args)
            trace_ide_attempt(attempt, name=prepared.spec.name, semantics=prepared.semantics)
            ctx = get_current_runtime_context()
            if ctx is None or not is_owner(ctx.user_role):
                raise ExternalToolError("ide_owner_context_required")
            # CONN-5A : une mission se reconnait au double verrou du HandlerContext,
            # pas au seul task_id du RuntimeContext, que le chat pose aussi pour la
            # tache de son tour. Le perimetre est revalide contre le TaskOrchestrator.
            handler_ctx = getattr(self.registry, "_v2_context", None)
            scope = derive_mission_scope(handler_ctx, runtime_task_id=ctx.task_id, caller=caller)
            if getattr(self.registry, "_allowed_tools_hard", False):
                allowed = getattr(self.registry, "_allowed_tools", None)
                if allowed is not None and name not in allowed:
                    raise ExternalToolError("ide_tool_outside_run_scope")
            required = _required_parameters(prepared)
            if scope is not None:
                # LOT L5-3c-4 : le catalogue de SA mission, pas celui de la
                # proprietaire. Sans cela, `expected_session` rend le projet de
                # l'utilisateur et l'autorisation refuse par
                # `ide_mission_workspace_mismatch` - le refus que L5-3b voulait
                # lever et qui survivait a L5-3b-bis : ouvrir l'instance ne servait
                # a rien tant que personne ne la REGARDAIT.
                snapshot, prepared = self._vue_de_la_mission(scope, name, args, snapshot, prepared)
                ide_workspace = self.service.expected_session(snapshot).workspace_path
                if canonical_workspace(ide_workspace) != scope.mission_root:
                    # LOT L5-3b : l'IDE disponible n'est pas celle de la mission.
                    # Rien en production n'ouvrait l'IDE sur un dossier de mission
                    # (`navigate` et `ide_launch` sont `forbidden`, `lumena_ide`
                    # ferme par L5-2) : tout appel `ide__*` d'une mission echouait
                    # donc par `ide_mission_workspace_mismatch`. Seul le canari
                    # produisait cet etat, par un `navigate` EXTERNE.
                    await self._ouvrir_instance_de_mission(scope)
                    try:
                        snapshot, prepared = self._vue_de_la_mission(
                            scope, name, args, snapshot, prepared)
                        ide_workspace = self.service.expected_session(snapshot).workspace_path
                    except Exception:
                        # Le catalogue n'a pas encore l'instance : l'autorisation
                        # ci-dessous refuse comme avant. Refus assume, pas silencieux.
                        pass
                authorize_mission_call(scope, prepared.semantics, ide_workspace, required_parameters=required)
            # Hors mission : CONN-4 must authorize scoped targets, leases and host
            # confirmations before opening other effects. Model-supplied confirmed is NOT consent.
            #
            # LOT CONN-6a : la regle est SEMANTIQUE, plus une liste de deux noms.
            # Celle-ci datait de CONN-4 et n'a jamais suivi le catalogue : mesure du
            # 23 septembre 2026, 41 des 135 commandes auditees sont en lecture pure
            # sans confirmation, et 2 seulement passaient. **39 lectures etaient
            # refusees sans rien proteger** - il n'y a aucun effet a autoriser pour
            # une lecture. Le fail-closed etait pose EN ATTENDANT ce lot.
            #
            # Ce qui borne la lecture n'est pas ici mais cote IDE :
            # `resolveWorkspacePath` (`workspaceSecurity.ts`) refuse toute cible hors
            # du workspace actif, celui que l'utilisateur a lui-meme ouvert. La voie
            # native, elle, lit tout le PC depuis L1d-3 : cette garde etait donc plus
            # stricte que la native pour une action plus restreinte.
            #
            # Les DEUX conditions restent exigees : aucun effet ET aucune
            # confirmation. Ecritures, taches, commandes et terminal demeurent
            # fermes au chat - trois gels de CONN-5 le figent.
            #
            # LOT CONN-6b : `UI_STATE_ONLY` rejoint `READ_ONLY`. Ces 29 commandes ne
            # touchent ni le disque, ni un processus, ni un reglage - elles ouvrent un
            # fichier, vont a une definition, affichent un diff, basculent un panneau.
            # Lumena pouvait DIRE ou une fonction est definie, sans pouvoir l'OUVRIR.
            #
            # Le risque est borne par un FAIT verifie, pas par un avis : les 29 sont
            # `mission_policy: forbidden` SANS EXCEPTION. Le seul appelant possible
            # est donc le chat, ou l'utilisateur est devant son ecran et vient de le
            # demander. Un test fige ce compte : si une seule cessait d'etre
            # interdite en mission, le lot changerait de nature.
            #
            # LOT CONN-7d : le CYCLE DE VIE de l'IDE echappe a cette regle, et lui
            # seul. Journal du 23 septembre, 21 h 35 : Charles demande a Lumena de
            # lancer son IDE, elle trouve le bon outil, et se fait refuser -
            # `ide_launch` n'etant ni une lecture ni une navigation. Apres ce refus
            # elle a tente `tasklist`, `netstat`, `wmic`, tous bloques par L2-1,
            # puis a **contourne par le PowerShell MCP**. Une porte legitime fermee
            # fabrique la recherche des mauvaises.
            #
            # La distinction est SEMANTIQUE, jamais nominative (un gel l'exige) :
            # les 135 commandes du catalogue sont `ProviderKind.IDE` - elles partent
            # VERS une IDE connectee. `ide_launch` est `NATIVE` : il ne part nulle
            # part, c'est Lumena qui lance sa propre application.
            #
            # Trois bornes, toutes deja en place et chacune verifiee par un test :
            # `mission_policy: FORBIDDEN` (aucune mission ne lance d'IDE) ; l'outil
            # n'existe au catalogue qu'a l'etat `launch_only`, donc seulement quand
            # AUCUNE IDE n'est connectee ; et le contexte proprietaire est exige bien
            # en amont.
            elif prepared.semantics.provider_kind is not ProviderKind.IDE:
                pass
            elif (prepared.semantics.effect not in {ToolEffect.READ_ONLY, ToolEffect.UI_STATE_ONLY}
                    or prepared.semantics.confirmation is not Confirmation.NEVER):
                raise ExternalToolError("ide_host_authorization_not_connected")
            # LOT CONN-7d — le CYCLE DE VIE s'execute LOCALEMENT, jamais par envoi.
            #
            # `expected_session` exige une session negociee. Or `ide_launch` n'existe
            # au catalogue qu'a l'etat `launch_only`, c'est-a-dire precisement quand
            # AUCUNE IDE n'est connectee : il n'a donc jamais de session, et le rail
            # levait `ide_snapshot_stale`. C'est le second refus du run de Charles a
            # 21 h 37 - et mon premier test de ce lot ne l'avait pas vu, se contentant
            # de verifier que le refus d'AUTORISATION avait disparu.
            #
            # Il n'y a aucune IDE a qui envoyer la demande : c'est pour cela qu'on la
            # lance. Le handler natif `_handle_ide_launch` fait le travail.
            if prepared.semantics.provider_kind is not ProviderKind.IDE:
                return await self._executer_cycle_de_vie(name, args, prepared, attempt)
            expected = self.service.expected_session(snapshot)
            write = None
            execution_plan = None
            if scope is not None:
                # Juste avant l'envoi, ce qui a ete autorise doit l'etre encore.
                if derive_mission_scope(handler_ctx, runtime_task_id=ctx.task_id, caller=caller) != scope:
                    raise MissionScopeError("ide_mission_scope_divergent")
                authorize_mission_call(scope, prepared.semantics, expected.workspace_path, required_parameters=required)
                if is_mission_content_write(prepared.semantics, required):
                    # CONN-5B-1 : meme cible et memes gardes que write_file natif.
                    write = prepare_mission_write(scope, handler_ctx, prepared.parameters)
                    # CONN-5B-2 : sans revalidation Electron (protocole 4), aucune ecriture de mission.
                    # Verifie APRES les gardes natifs : un refus garde sa vraie raison.
                    if getattr(expected, "protocol", 0) < MISSION_SCOPE_PROTOCOL_VERSION:
                        raise MissionScopeError("ide_mission_scope_unsupported")
                elif is_mission_execution(prepared.semantics):
                    # CONN-5C-1 : la commande REELLE, retrouvee dans la liste de l'IDE et
                    # jugee par les gardes de run_command ; la version vient apres eux.
                    if prepared.semantics.tool_name == "ide__command_run":
                        # CONN-5C-2 : commande unique, sans liste a consulter.
                        execution_plan = prepare_mission_command(scope, handler_ctx, prepared.parameters)
                    else:
                        listing = await self.service.bridge.send_command(
                            listing_action(prepared.semantics), {}, expected_snapshot=expected)
                        execution_plan = prepare_mission_execution(
                            scope, handler_ctx, prepared.semantics, prepared.parameters, listing)
                    if getattr(expected, "protocol", 0) < MISSION_SCOPE_PROTOCOL_VERSION:
                        raise MissionScopeError("ide_mission_scope_unsupported")
            envelope = {}
            if write is not None:
                envelope = {"mission_scope": mission_envelope_scope(scope, write)}
            elif execution_plan is not None:
                envelope = {"mission_scope": mission_execution_envelope(scope, execution_plan)}
            async with mission_write_guard(write, handler_ctx):
                effect_ticket = external_effect_cache().begin(prepared.semantics, expected.workspace_id)
                try:
                    result = await self.service.bridge.send_command(
                        name.removeprefix("ide__"), prepared.parameters, expected_snapshot=expected, **envelope,
                    )
                except BaseException:
                    # LOT CONN-7b : le transport a leve, donc l'operation n'a JAMAIS
                    # eu lieu. Il n'y a rien a prouver, donc rien a suspendre.
                    #
                    # Sans ce rattrapage, le ticket restait actif A VIE dans un
                    # singleton de MODULE, et `observation_cache_epoch` rendait
                    # `None` : le cache d'observation etait desactive pour tout le
                    # reste de la session, sans qu'aucun journal ne le dise.
                    #
                    # La distinction est le coeur du lot : une operation dont la FIN
                    # n'est pas prouvee garde son ticket - c'est la conception, dite
                    # quelques lignes plus bas. Une operation qui n'a jamais commence
                    # n'a pas de fin a prouver.
                    external_effect_cache().abandonner(effect_ticket)
                    raise
                if type(result) is not dict:
                    raise ExternalToolError("ide_result_invalid")
                output = json.dumps(result, ensure_ascii=False)
                execution = ide_execution_result(prepared, result, expected, output)
                external_effect_cache().complete(effect_ticket, execution)
                evidence = verify_mission_write(execution, prepared.semantics, scope, expected, write)
            if execution_plan is not None:
                # CONN-5C-1 : suivi interne borne ; seule une preuve verifiee fait un succes.
                completion, verified, same_command, expired = await follow_mission_execution(
                    self._internal_dispatch(expected), execution, prepared.semantics, scope, expected,
                    execution_plan, handler_ctx)
                # Un lancement ne quitte le cache d'effets qu'avec sa fin verifiee ; sans elle
                # (delai depasse, preuve invalide) la suspension du cache reste, par conception.
                external_effect_cache().complete(effect_ticket, execution, evidence=verified)
                evidence = verified if same_command else None
                output = json.dumps({"launch": result, "completion": completion}, ensure_ascii=False)
                if expired:
                    output = "IDE: ide_mission_execution_timeout\n" + output
                trace_ide_attempt(attempt, name=prepared.spec.name, semantics=prepared.semantics, result=execution,
                                  reason="timeout" if expired else "completed")
                success = result.get("success") is True and evidence is not None and evidence.success
                return Observation(content=output, success=success, execution=execution,
                                   execution_evidence=evidence)
            trace_ide_attempt(attempt, name=prepared.spec.name, semantics=prepared.semantics, result=execution,
                              reason="pending" if execution and execution.completed_at is None else "completed")
            return Observation(content=output, success=result.get("success") is True, execution=execution,
                               execution_evidence=evidence)
        except ExternalToolError as exc:
            trace_ide_attempt(attempt, name=prepared.spec.name if prepared else "ide__unresolved",
                              semantics=prepared.semantics if prepared else None, reason="refused")
            # CONN-7a : le refus porte sa cause et son geste dans `guidance`.
            # `content` reste EXACTEMENT le code : c'est un contrat, fige par 37
            # egalites strictes dont les gels de mission CONN-5.
            return Observation(content=f"IDE: {exc}", success=False,
                               guidance=expliquer_erreur_ide(str(exc)))
        except Exception as exc:
            trace_ide_attempt(attempt, name=prepared.spec.name if prepared else "ide__unresolved",
                              semantics=prepared.semantics if prepared else None, reason="dispatch_failed")
            logger.warning("IDE dispatch failed ({})", type(exc).__name__)
            return Observation(content="IDE: ide_dispatch_failed", success=False,
                               guidance=expliquer_erreur_ide("ide_dispatch_failed"))
