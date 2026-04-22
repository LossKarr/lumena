"""
🌟 LUMENA - Scheduler (Planificateur de Tâches)

Permet à LUMENA de planifier et exécuter des tâches automatiques.
C'est le cœur de son autonomie 24/7.

Fonctionnalités:
- Support expressions CRON (ex: "0 9 * * *" = 9h tous les jours)
- Intervalles dynamiques en millisecondes
"""

import asyncio
import hashlib
import json
import re
from typing import Optional, List, Dict, Any, Callable, Awaitable, Union
from dataclasses import dataclass, field
from datetime import datetime, timedelta

# ── Flag global : agent occupé (boucle ReAct active) ─────────────────────────
_AGENT_BUSY: bool = False

# ── Flag global : création de skill autonome en cours ────────────────────────
# Distinct de _AGENT_BUSY pour éviter le deadlock scheduler ↔ think_and_act
_SKILL_CREATION_BUSY: bool = False


def set_agent_busy(busy: bool) -> None:
    """Signale que l'agent (boucle ReAct) est actif ou non.
    Appelé par core.py avant/après react.run()."""
    global _AGENT_BUSY
    _AGENT_BUSY = busy


def is_agent_busy() -> bool:
    """Retourne True si la boucle ReAct est en cours d'exécution."""
    return _AGENT_BUSY


def is_skill_creation_busy() -> bool:
    """Retourne True si think_and_act_silent tourne pour la création de skill."""
    return _SKILL_CREATION_BUSY
from enum import Enum
import os
from pathlib import Path
from time import perf_counter
from loguru import logger

# Support CRON optionnel
try:
    from croniter import croniter
    CRONITER_AVAILABLE = True
except ImportError:
    CRONITER_AVAILABLE = False
    logger.debug("croniter non installé. Support CRON désactivé. pip install croniter")


def validate_cron_expr(cron_expr: str, with_error: Optional[bool] = None):
    """
    Phase 3.3: Valide une expression CRON explicitement.
    
    Args:
        cron_expr: Expression CRON à valider
        with_error: Si True, retourne (bool, error_message). Sinon, retourne bool.
        
    Returns:
        bool ou tuple(bool, error_message) selon with_error
    """
    def _ret(ok: bool, err: Optional[str] = None):
        if with_error:
            return ok, err
        return ok

    if not cron_expr or not isinstance(cron_expr, str):
        return _ret(False, "Expression CRON vide ou invalide")
    
    if not CRONITER_AVAILABLE:
        return _ret(False, "croniter non installé")
    
    try:
        from datetime import datetime
        cron = croniter(cron_expr, datetime.now())
        # Essayer de calculer les 2 prochaines exécutions pour vérifier
        cron.get_next(datetime)
        cron.get_next(datetime)
        return _ret(True, None)
    except Exception as e:
        return _ret(False, str(e))


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class TaskFrequency(Enum):
    """Fréquence d'une tâche."""
    ONCE = "once"                 # Une seule fois
    EVERY_MINUTE = "every_minute"
    EVERY_5_MINUTES = "every_5_minutes"
    EVERY_15_MINUTES = "every_15_minutes"
    EVERY_30_MINUTES = "every_30_minutes"
    EVERY_45_MINUTES = "every_45_minutes"
    EVERY_HOUR = "hourly"
    EVERY_DAY = "daily"
    EVERY_WEEK = "weekly"
    CRON = "cron"                 # Expression CRON (ex: "0 9 * * *")
    INTERVAL_MS = "interval_ms"  # Intervalle en millisecondes


class TaskStatus(Enum):
    """Statut d'une tâche."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ScheduledTask:
    """Une tâche planifiée."""
    id: str
    name: str
    description: str
    frequency: TaskFrequency
    handler_name: str  # Nom du handler à appeler
    
    # Planification
    next_run: datetime
    last_run: Optional[datetime] = None
    
    # Config CRON/Intervalle (nouveau)
    cron_expr: Optional[str] = None      # Expression CRON (ex: "0 9 * * *")
    interval_ms: Optional[int] = None    # Intervalle en millisecondes
    timezone: Optional[str] = None       # Timezone pour CRON
    
    # Stats
    run_count: int = 0
    success_count: int = 0
    fail_count: int = 0
    
    # État
    status: TaskStatus = TaskStatus.PENDING
    enabled: bool = True
    
    # Config
    max_retries: int = 3
    timeout_seconds: int = 300
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def calculate_next_run(self):
        """
        Calcule la prochaine exécution.
        
        Supporte:
        - Fréquences prédéfinies (EVERY_HOUR, EVERY_DAY, etc.)
        - Expressions CRON (ex: "0 9 * * *" = 9h tous les jours)
        - Intervalles en millisecondes
        """
        now = datetime.now()
        
        if self.frequency == TaskFrequency.ONCE:
            # Pas de répétition
            pass
        
        elif self.frequency == TaskFrequency.CRON:
            # Expression CRON
            if CRONITER_AVAILABLE and self.cron_expr:
                try:
                    cron = croniter(self.cron_expr, now)
                    self.next_run = cron.get_next(datetime)
                    logger.debug(f"CRON '{self.cron_expr}' -> next: {self.next_run}")
                except Exception as e:
                    logger.error(f"Erreur CRON '{self.cron_expr}': {e}")
                    self.next_run = now + timedelta(hours=1)  # Fallback
            else:
                logger.warning("croniter non disponible, fallback 1h")
                self.next_run = now + timedelta(hours=1)
        
        elif self.frequency == TaskFrequency.INTERVAL_MS:
            # Intervalle en millisecondes
            if self.interval_ms and self.interval_ms > 0:
                self.next_run = now + timedelta(milliseconds=self.interval_ms)
            else:
                self.next_run = now + timedelta(minutes=5)  # Fallback
        
        elif self.frequency == TaskFrequency.EVERY_MINUTE:
            self.next_run = now + timedelta(minutes=1)
        elif self.frequency == TaskFrequency.EVERY_5_MINUTES:
            self.next_run = now + timedelta(minutes=5)
        elif self.frequency == TaskFrequency.EVERY_15_MINUTES:
            self.next_run = now + timedelta(minutes=15)
        elif self.frequency == TaskFrequency.EVERY_30_MINUTES:
            self.next_run = now + timedelta(minutes=30)
        elif self.frequency == TaskFrequency.EVERY_45_MINUTES:
            self.next_run = now + timedelta(minutes=45)
        elif self.frequency == TaskFrequency.EVERY_HOUR:
            self.next_run = now + timedelta(hours=1)
        elif self.frequency == TaskFrequency.EVERY_DAY:
            self.next_run = now + timedelta(days=1)
        elif self.frequency == TaskFrequency.EVERY_WEEK:
            self.next_run = now + timedelta(weeks=1)


class LumenaScheduler:
    """
    ⏰ Planificateur de tâches de LUMENA
    
    Gère les tâches automatiques et périodiques.
    Permet à LUMENA de fonctionner de manière autonome 24/7.
    """
    
    _task_counter: int = 0  # Compteur global pour IDs uniques
    
    def __init__(self, data_dir: Optional[Path] = None):
        self.tasks: Dict[str, ScheduledTask] = {}
        self.handlers: Dict[str, Callable[..., Awaitable[Any]]] = {}
        self.data_dir = data_dir
        
        self._running = False
        self._loop_task: Optional[asyncio.Task] = None
        
        if self.data_dir:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self.tasks_file = self.data_dir / "scheduled_tasks.json"
        
        # Enregistrer les handlers par défaut
        self._register_default_handlers()
        
        logger.info("⏰ Scheduler initialisé")
    
    def _register_default_handlers(self):
        """Enregistre les handlers par défaut + handlers ops production."""
        
        async def handler_curiosity_update():
            """Met à jour le module de curiosité."""
            from .curiosity import get_curiosity_module
            module = get_curiosity_module()
            action = module.update()
            if action:
                logger.info(f"Action autonome: {action.description}")
            return action
        
        # ── Handlers ops production (remplacent les placeholders) ──
        try:
            from .ops_handlers import OPS_HANDLERS, HANDLER_TIMEOUTS
            for name, handler_fn in OPS_HANDLERS.items():
                self.handlers[name] = handler_fn
            logger.info(f"⚙️ {len(OPS_HANDLERS)} handlers ops enregistrés")
        except ImportError as e:
            logger.warning(f"⚠️ ops_handlers non disponible: {e}")
            # Fallback : garder les placeholders
            async def handler_memory_cleanup():
                logger.debug("Nettoyage mémoire (placeholder)")
                return True
            async def handler_health_check():
                logger.debug("Health check OK")
                return {"status": "healthy", "timestamp": datetime.now().isoformat()}
            async def handler_save_state():
                logger.debug("Sauvegarde état")
                return True
            self.handlers["memory_cleanup"] = handler_memory_cleanup
            self.handlers["health_check"] = handler_health_check
            self.handlers["save_state"] = handler_save_state

        async def handler_daily_skill_autonomy():
            """Cycle quotidien: Lumena choisit elle-même un skill utile via le LLM,
            basé sur ses interactions récentes et les tendances IA du moment."""
            if not _env_flag("LUMENA_AUTONOMY_DAILY_SKILL_ENABLE", True):
                logger.debug("Daily skill autonomy désactivé par env")
                return {"success": True, "status": "disabled_by_env"}

            dry_run = _env_flag("LUMENA_AUTONOMY_DAILY_SKILL_DRY_RUN", False)
            root = Path(__file__).parent.parent.parent

            # ── Guard anti-doublon ────────────────────────────────────────────
            from .self_improve import get_self_improver
            improver = get_self_improver(root)
            state = improver._load_daily_skill_state()
            today = datetime.now().strftime("%Y-%m-%d")
            if state.get("last_run_date") == today:
                logger.info(
                    "Daily skill autonomy: status=already_done_today skill={}",
                    state.get("last_skill_name", ""),
                )
                return {
                    "success": True,
                    "status": "already_done_today",
                    "date": today,
                    "created": False,
                    "skill_name": state.get("last_skill_name", ""),
                }

            # ── Collecter le contexte pour le LLM ────────────────────────────
            try:
                from ..skills import get_skill_loader
                loader = get_skill_loader()
                existing_skills = list(loader.list_skills())
            except Exception:
                existing_skills = []

            # Journal récent (10 dernières entrées)
            journal_snippets: list[str] = []
            try:
                from src.utils.paths import JOURNAL_JSON
                journal_path = JOURNAL_JSON
                if journal_path.exists():
                    entries = json.loads(journal_path.read_text(encoding="utf-8"))
                    for e in entries[-10:]:
                        content = str(e.get("content", ""))[:200]
                        if content:
                            journal_snippets.append(content)
            except Exception as e:
                logger.debug(f"Lecture journal pour skill: {e}")

            # Dernières erreurs connues
            error_snippets: list[str] = []
            for err_file in ["test_error.txt", "_test_output.txt"]:
                try:
                    p = root / err_file
                    if p.exists():
                        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                        error_snippets.extend(lines[-5:])
                except Exception as e:
                    logger.debug(f"Lecture err file: {e}")

            # ── Guard : ne pas lancer si une autre création est en cours ─────
            global _SKILL_CREATION_BUSY
            if _SKILL_CREATION_BUSY:
                logger.debug("Daily skill: création déjà en cours, skip ce cycle")
                return {"success": True, "status": "creation_busy", "date": today, "created": False}

            # ── Appel think_and_act_silent — boucle ReAct consciente ──────────
            llm_candidate: dict = {}
            try:
                from ..core import get_lumena
                core = get_lumena()
                if core and hasattr(core, "think_and_act_silent"):
                    skills_list = "\n".join(f"- {s}" for s in existing_skills[:40]) or "(aucun)"
                    journal_text = "\n".join(f"  • {s}" for s in journal_snippets) or "(vide)"
                    errors_text = "\n".join(error_snippets[-6:]) or "(aucune)"

                    task_prompt = f"""Tu es Lumena. Crée UN skill utile aujourd'hui ({today}).

ÉTAPES (UNE action par réponse, attends l'OBSERVATION avant la suivante) :
1. ACTION: memory_search → chercher tes erreurs répétées ou manques concrets.
2. ACTION: create_skill → name et content obligatoires. Le content DOIT inclure:
   - Section "## Quand l'utiliser" avec 2 cas concrets
   - Section "## Instructions" avec des étapes actionnables
   - Section "## Exemples" avec input/output réels
   NE PAS créer de skill vague/abstrait ("introspection", "auto-analyse").
   BON: "api-error-retry" avec code de retry. MAUVAIS: "emotional-analysis".
3. ACTION: FINAL → confirmer SEULEMENT après OBSERVATION de create_skill.

⛔ INTERDIT: imaginer les résultats. Attends chaque OBSERVATION.
⛔ INTERDIT: appeler write_file après create_skill (il écrit déjà SKILL.md).

SKILLS EXISTANTS : {", ".join(existing_skills[:40]) or "(aucun)"}
ERREURS RÉCENTES : {errors_text[:200]}"""

                    # Outils strictement nécessaires pour créer un skill
                    _skill_tools = [
                        "memory_search", "memory_stats", "web_search",
                        "read_file", "write_file", "list_directory",
                        "find_files", "create_skill", "list_skills",
                        "get_time", "read_journal",
                    ]

                    _SKILL_CREATION_BUSY = True
                    try:
                        raw_result = await core.think_and_act_silent(
                            task_prompt, timeout=180.0,
                            allowed_tools=_skill_tools,
                        )
                    finally:
                        _SKILL_CREATION_BUSY = False

                    # Extraire le nom du skill créé depuis la réponse ReAct
                    # (create_skill() enregistre le skill, on récupère son nom)
                    if raw_result:
                        name_match = re.search(
                            r'create_skill[\s\S]*?name["\s]*[:=]["\s]*([a-z0-9][a-z0-9\-]{1,38})',
                            raw_result, re.IGNORECASE
                        )
                        # Chercher aussi dans le texte de confirmation
                        confirm_match = re.search(
                            r"[Ss]kill[\s'\"]+(([a-z][a-z0-9\-]{2,38}))[\s'\"]+(?:créé|created|validé)",
                            raw_result
                        )
                        detected_name = (
                            (name_match.group(1) if name_match else None)
                            or (confirm_match.group(1) if confirm_match else None)
                        )
                        if detected_name:
                            llm_candidate = {
                                "name": detected_name,
                                "description": f"Skill créé en autonomie par Lumena le {today}",
                                "content": "",   # déjà écrit par think_and_act_silent
                                "reason": f"Créé via boucle ReAct consciente",
                                "_via_react": True,
                            }
                            logger.info(
                                "Daily skill: skill '{}' détecté via boucle ReAct",
                                detected_name,
                            )
                        else:
                            # ReAct a tourné mais on ne sait pas quel skill a été créé
                            # Chercher dans le dossier skills/ le dernier créé aujourd'hui
                            skills_dir = root / "skills"
                            from datetime import date as _date
                            today_ts = _date.today()
                            new_skills = [
                                d for d in skills_dir.iterdir()
                                if d.is_dir()
                                and (d / "SKILL.md").exists()
                                and d.name not in existing_skills
                            ]
                            if new_skills:
                                newest = max(new_skills, key=lambda d: d.stat().st_mtime)
                                llm_candidate = {
                                    "name": newest.name,
                                    "description": f"Skill créé autonomement le {today}",
                                    "content": "",
                                    "reason": "Détecté par scan dossier",
                                    "_via_react": True,
                                }
                                logger.info(
                                    "Daily skill: nouveau skill détecté par scan: {}",
                                    newest.name,
                                )

            except Exception as e:
                _SKILL_CREATION_BUSY = False
                logger.warning("think_and_act_silent échoué ({}), fallback llm.chat", e)
                # ── Fallback vers llm.chat simple ────────────────────────────
                try:
                    from ..core import get_lumena as _gl2
                    core2 = _gl2()
                    if core2 and hasattr(core2, "llm"):
                        skills_list2 = "\n".join(f"- {s}" for s in existing_skills[:40]) or "(aucun)"
                        fallback_prompt = f"""Tu es Lumena. Choisis UN skill à créer aujourd'hui ({today}).
Skills existants: {skills_list2}
Réponds UNIQUEMENT en JSON: {{"name":"kebab-case","description":"...","content":"# Titre\\n\\n## Quand l'utiliser\\n...\\n## Instructions\\n1. ...\\n## Bonnes pratiques\\n...","reason":"..."}}"""
                        raw2 = await core2.llm.chat([{"role": "user", "content": fallback_prompt}])
                        jm = re.search(r'\{[\s\S]*\}', raw2 or "")
                        if jm:
                            p2 = json.loads(jm.group())
                            if p2.get("name") and p2.get("description"):
                                llm_candidate = p2
                except Exception as e2:
                    logger.debug("Fallback llm.chat aussi échoué: {}", e2)

            # ── Fallback si tout a échoué ─────────────────────────────────────
            if not llm_candidate:
                return {
                    "success": True,
                    "status": "no_op",
                    "date": today,
                    "created": False,
                    "reason": "LLM indisponible ou skill non détecté.",
                }

            skill_name = improver._sanitize_skill_name(llm_candidate["name"])
            _via_react = llm_candidate.get("_via_react", False)

            # Vérifier que le skill n'existe pas déjà (sauf si créé via ReAct — il existe déjà)
            normalized_existing = {improver._sanitize_skill_name(s) for s in existing_skills}
            if not _via_react and skill_name in normalized_existing:
                state["last_run_date"] = today
                state["last_skill_name"] = skill_name
                state["last_status"] = "already_exists"
                improver._save_daily_skill_state(state)
                return {
                    "success": True,
                    "status": "already_exists",
                    "date": today,
                    "created": False,
                    "skill_name": skill_name,
                }

            if dry_run:
                return {
                    "success": True,
                    "status": "dry_run",
                    "date": today,
                    "created": False,
                    "skill_name": skill_name,
                    "reason": llm_candidate.get("reason", ""),
                }

            # ── Créer le skill (uniquement si pas déjà fait via ReAct) ───────
            created_ok: bool
            if _via_react:
                # Le skill a déjà été créé par think_and_act_silent via create_skill()
                skill_dir_check = root / "skills" / skill_name
                created_ok = skill_dir_check.exists() and (skill_dir_check / "SKILL.md").exists()
                logger.info(
                    "Daily skill: skill '{}' créé via boucle ReAct (exists={})",
                    skill_name, created_ok,
                )
            else:
                try:
                    from ..skills import create_skill
                    create_result = create_skill(
                        name=skill_name,
                        description=llm_candidate["description"],
                        with_script=False,
                    )
                    created_ok = str(create_result).strip().startswith("✅")
                except Exception as e:
                    created_ok = False

            # ── Écrire le contenu LLM dans SKILL.md (uniquement mode llm.chat fallback) ──
            if not _via_react and created_ok and llm_candidate.get("content"):
                skill_dir = root / "skills" / skill_name
                skill_md = skill_dir / "SKILL.md"
                if skill_dir.exists():
                    try:
                        frontmatter = (
                            f"---\nname: {skill_name}\n"
                            f"description: \"{llm_candidate['description']}\"\n---\n\n"
                        )
                        skill_md.write_text(
                            frontmatter + llm_candidate["content"],
                            encoding="utf-8",
                        )
                    except Exception as e:
                        logger.debug("SKILL.md write failed: {}", e)

            # ── Récupérer core pour la validation ────────────────────────────
            try:
                from ..core import get_lumena as _gl_val
                core = _gl_val()
            except Exception:
                core = None

            # ── Validation LLM — elle relit son skill et donne 2 exemples concrets ──
            validation_passed = True
            if created_ok:
                skill_dir_val = root / "skills" / skill_name
                skill_md_val = skill_dir_val / "SKILL.md"
                if skill_dir_val.exists() and skill_md_val.exists():
                    try:
                        skill_content = skill_md_val.read_text(encoding="utf-8")
                        val_prompt = (
                            f"Tu viens de créer le skill suivant:\n\n"
                            f"{skill_content[:2000]}\n\n"
                            f"Pour VALIDER ce skill, donne-moi exactement 2 exemples CONCRETS "
                            f"de situations où tu l'utiliserais dans ton travail quotidien. "
                            f"Sois précise et pratique — des cas réels, pas génériques. "
                            f"Si le skill est trop vague, inutile ou fait doublon avec tes skills "
                            f"existants — réponds INVALIDE suivi d'une courte raison. "
                            f"Sinon réponds VALIDE puis liste tes 2 exemples."
                        )
                        val_response = await core.llm.chat(
                            [{"role": "user", "content": val_prompt}]
                        )
                        if isinstance(val_response, str) and \
                                val_response.strip().upper().startswith("INVALIDE"):
                            validation_passed = False
                            logger.warning(
                                "📛 Skill '{}' rejeté à l'auto-validation: {}",
                                skill_name, val_response[:150],
                            )
                            import shutil
                            try:
                                shutil.rmtree(skill_dir_val)
                                logger.debug("Skill '{}' supprimé (validation échouée)", skill_name)
                            except Exception as del_e:
                                logger.debug("Suppression skill échouée: {}", del_e)
                        else:
                            logger.info(
                                "✅ Skill '{}' validé — exemples: {}",
                                skill_name,
                                str(val_response)[:200] if val_response else "(réponse vide)",
                            )
                    except Exception as val_e:
                        # Ne jamais bloquer sur erreur LLM — on conserve le skill par défaut
                        logger.debug("Validation skill erreur LLM (conservé par défaut): {}", val_e)

            # ── Reload dynamique — le skill est dispo immédiatement sans restart ──
            if created_ok and validation_passed:
                try:
                    from ..skills.loader import get_skill_loader as _gsl
                    _gsl().register_single(root / "skills" / skill_name)
                    logger.info("🔄 Skill '{}' rechargé dans le loader (dispo immédiatement)", skill_name)
                except Exception as _reload_e:
                    logger.debug("Reload skill loader non bloquant: {}", _reload_e)

            final_status = (
                "created" if (created_ok and validation_passed)
                else "validation_failed" if (created_ok and not validation_passed)
                else "create_failed"
            )
            state["last_run_date"] = today
            state["run_count"] = int(state.get("run_count", 0)) + 1
            state["last_skill_name"] = skill_name
            state["last_skill_reason"] = llm_candidate.get("reason", "")
            state["last_status"] = final_status
            if not created_ok or not validation_passed:
                state["fail_count"] = int(state.get("fail_count", 0)) + 1
            improver._save_daily_skill_state(state)

            logger.info(
                "Daily skill autonomy: status={} skill={} created={} reason={}",
                state["last_status"],
                skill_name,
                created_ok,
                llm_candidate.get("reason", "")[:80],
            )
            return {
                "success": created_ok and validation_passed,
                "status": final_status,
                "date": today,
                "created": created_ok,
                "validated": validation_passed,
                "skill_name": skill_name,
                "reason": llm_candidate.get("reason", ""),
            }
        
        self.handlers["curiosity_update"] = handler_curiosity_update
        # memory_cleanup, health_check, save_state sont enregistrés via ops_handlers
        # (ou via fallback placeholders si ops_handlers indisponible)
        self.handlers["daily_skill_autonomy"] = handler_daily_skill_autonomy

        async def handler_daily_code_analysis():
            """Analyse quotidienne du code source — propose_improvement() sur les fichiers clés."""
            try:
                from .self_improve import get_self_improver
                improver = get_self_improver(Path(__file__).parent.parent.parent)
                key_files = ["core.py", "reasoning/react.py", "tools/tool_system.py", "autonomy/daemon.py"]
                results = []
                for f in key_files:
                    try:
                        analysis = improver.propose_improvement(f)
                        if analysis and "Aucune amélioration" not in analysis:
                            results.append(analysis)
                    except Exception as e:
                        logger.debug(f"Auto-analyse {f}: {e}")
                if results:
                    logger.info(f"🔍 Auto-analyse: {len(results)} fichier(s) avec suggestions")
                    for r in results:
                        logger.info(r[:200])
                return {"success": True, "suggestions": len(results)}
            except Exception as e:
                logger.debug(f"Auto-analyse code échouée: {e}")
                return {"success": False, "error": str(e)}

        self.handlers["daily_code_analysis"] = handler_daily_code_analysis

        async def handler_weekly_auto_improve():
            """Pipeline d'auto-amélioration hebdomadaire de Lumena."""
            retrain_lock = None
            try:
                pipeline_script = (
                    Path(__file__).parent.parent.parent
                    / "models" / "lumena-v1.0.0" / "7_auto_retrain.py"
                )
                if not pipeline_script.exists():
                    logger.debug("7_auto_retrain.py non trouvé, pipeline ignoré")
                    return {"success": False, "reason": "script_not_found"}

                # Acquérir le retrain lock avant de lancer le pipeline
                try:
                    from ..autonomy.ops_handlers import _acquire_retrain_lock
                    retrain_lock = _acquire_retrain_lock()
                    if retrain_lock is None:
                        logger.warning("⚠️ weekly_auto_improve: retrain_lock déjà actif, skip")
                        return {"success": False, "reason": "retrain_lock_active"}
                except ImportError:
                    logger.debug("ops_handlers non disponible, pas de retrain lock")

                import subprocess, sys
                result = await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, str(pipeline_script)],
                    capture_output=True,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    timeout=14400,  # 4h max
                )
                success = result.returncode == 0
                if success:
                    logger.success("✅ Pipeline auto-amélioration terminé")
                else:
                    logger.warning(f"⚠️ Pipeline auto-amélioration: {result.stderr[:200]}")
                return {"success": success, "returncode": result.returncode}
            except Exception as e:
                logger.warning(f"Handler weekly_auto_improve: {e}")
                return {"success": False, "error": str(e)}
            finally:
                # Toujours libérer le lock
                if retrain_lock is not None:
                    try:
                        retrain_lock.release()
                    except Exception as e:
                        logger.debug(f"Lock release weekly_auto_improve: {e}")

        self.handlers["weekly_auto_improve"] = handler_weekly_auto_improve
    
    def register_handler(self, name: str, handler: Callable[..., Awaitable[Any]]):
        """Enregistre un handler de tâche."""
        self.handlers[name] = handler
        logger.debug(f"Handler enregistré: {name}")
    
    def schedule(
        self,
        name: str,
        description: str,
        handler_name: str,
        frequency: TaskFrequency = TaskFrequency.ONCE,
        run_at: Optional[datetime] = None,
        cron_expr: Optional[str] = None,
        interval_ms: Optional[int] = None,
        metadata: Optional[Dict] = None
    ) -> ScheduledTask:
        """
        Planifie une nouvelle tâche.
        
        Args:
            name: Nom de la tâche
            description: Description
            handler_name: Nom du handler à appeler
            frequency: Fréquence de répétition
            run_at: Quand l'exécuter (défaut: maintenant)
            cron_expr: Expression CRON (ex: "0 9 * * *" = 9h chaque jour)
            interval_ms: Intervalle en millisecondes
            metadata: Données additionnelles
            
        Returns:
            La tâche créée
        
        Exemples CRON:
            "0 9 * * *"     -> Tous les jours à 9h
            "0 9 * * 1"     -> Tous les lundis à 9h
            "*/15 * * * *"  -> Toutes les 15 minutes
            "0 0 1 * *"     -> Le 1er de chaque mois à minuit
        """
        if handler_name not in self.handlers:
            raise ValueError(f"Handler inconnu: {handler_name}")
        
        # Phase 3.3: Validation CRON explicite
        if frequency == TaskFrequency.CRON and cron_expr:
            is_valid, error = validate_cron_expr(cron_expr, with_error=True)
            if not is_valid:
                raise ValueError(f"Expression CRON invalide '{cron_expr}': {error}")
        
        task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{LumenaScheduler._task_counter}"
        LumenaScheduler._task_counter += 1
        
        # Déterminer next_run initial
        initial_run = run_at or datetime.now()
        
        # Si CRON, calculer la prochaine exécution
        if frequency == TaskFrequency.CRON and cron_expr and CRONITER_AVAILABLE:
            try:
                cron = croniter(cron_expr, datetime.now())
                initial_run = cron.get_next(datetime)
            except Exception as e:
                logger.error(f"Expression CRON invalide '{cron_expr}': {e}")
        
        task = ScheduledTask(
            id=task_id,
            name=name,
            description=description,
            frequency=frequency,
            handler_name=handler_name,
            next_run=initial_run,
            cron_expr=cron_expr,
            interval_ms=interval_ms,
            metadata=metadata or {},
        )
        
        self.tasks[task_id] = task
        
        # Log avec détails CRON si applicable
        if frequency == TaskFrequency.CRON:
            logger.info(f"⏰ Tâche CRON planifiée: {name} ('{cron_expr}')")
        else:
            logger.info(f"⏰ Tâche planifiée: {name}")
        
        return task
    
    def schedule_cron(
        self,
        name: str,
        description: str,
        handler_name: str,
        cron_expr: str,
        metadata: Optional[Dict] = None
    ) -> ScheduledTask:
        """
        Raccourci pour planifier une tâche CRON.
        
        Args:
            name: Nom de la tâche
            description: Description
            handler_name: Handler à appeler
            cron_expr: Expression CRON
            
        Exemples:
            schedule_cron("Morning Check", "Vérification matinale", "health_check", "0 9 * * *")
        """
        return self.schedule(
            name=name,
            description=description,
            handler_name=handler_name,
            frequency=TaskFrequency.CRON,
            cron_expr=cron_expr,
            metadata=metadata
        )
    
    def cancel_task(self, task_id: str):
        """Annule une tâche."""
        if task_id in self.tasks:
            self.tasks[task_id].status = TaskStatus.CANCELLED
            self.tasks[task_id].enabled = False
            logger.info(f"Tâche annulée: {task_id}")

    def _normalize_handler_result(
        self,
        task: ScheduledTask,
        raw_result: Any,
        duration_ms: float,
        *,
        fallback_status: str,
        fallback_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Normalise la sortie d'un handler pour faciliter l'observabilité ops."""
        if isinstance(raw_result, dict):
            normalized = dict(raw_result)
        else:
            normalized = {"result": raw_result}

        success = normalized.get("success")
        if success is None:
            success = fallback_status not in {"error", "timeout"}
        success = bool(success)

        status = str(normalized.get("status") or fallback_status)
        reason = normalized.get("reason") or fallback_reason

        normalized["success"] = success
        normalized["status"] = status
        normalized["reason"] = reason
        normalized["task_id"] = task.id
        normalized["task_name"] = task.name
        normalized["handler"] = task.handler_name
        normalized["duration_ms"] = round(duration_ms, 2)
        normalized["timestamp"] = datetime.now().isoformat()

        # P3.4: contrat de livraison (manifest + proof)
        manifest = normalized.get("manifest")
        proof = normalized.get("proof")
        if manifest is not None and proof is not None:
            normalized["executed"] = (len(proof) == len(manifest))

        return normalized

    # ── Idempotence par clé déterministe (P4.3) ──────────────────────────

    @staticmethod
    def _compute_cron_window(task: ScheduledTask) -> str:
        """Fenêtre temporelle pour la clé d'idempotence.
        daily/weekly/once → 'YYYY-MM-DD', horaire/minutes → 'YYYY-MM-DD_HH'."""
        now = datetime.now()
        if task.frequency in (
            TaskFrequency.EVERY_MINUTE,
            TaskFrequency.EVERY_5_MINUTES,
            TaskFrequency.EVERY_15_MINUTES,
            TaskFrequency.EVERY_30_MINUTES,
            TaskFrequency.EVERY_45_MINUTES,
            TaskFrequency.EVERY_HOUR,
        ):
            return now.strftime("%Y-%m-%d_%H")
        # CRON: inspecter si sub-hourly
        if task.frequency == TaskFrequency.CRON and task.cron_expr:
            parts = task.cron_expr.strip().split()
            # If minute field is '*' or contains '/' → sub-hourly
            if len(parts) >= 1 and ("*" in parts[0] or "/" in parts[0]):
                return now.strftime("%Y-%m-%d_%H")
        return now.strftime("%Y-%m-%d")

    @staticmethod
    def _compute_task_key(task: ScheduledTask, cron_window: str) -> str:
        """Clé déterministe : handler_name:cron_window:payload_hash."""
        payload = json.dumps(task.metadata.get("params", {}), sort_keys=True)
        payload_hash = hashlib.sha256(payload.encode()).hexdigest()[:8]
        return f"{task.handler_name}:{cron_window}:{payload_hash}"

    def _load_idempotence_registry(self) -> Dict[str, Any]:
        """Charge le registre d'idempotence depuis l'état ops."""
        try:
            from .ops_handlers import _load_state
            state = _load_state()
            return state.get("_idempotence_registry", {})
        except Exception:
            return {}

    def _save_idempotence_entry(self, key: str, status: str, error_msg: str = ""):
        """Persiste une entrée dans le registre d'idempotence."""
        try:
            from .ops_handlers import _load_state, _save_state
            state = _load_state()
            reg = state.setdefault("_idempotence_registry", {})
            reg[key] = {"status": status, "ts": datetime.now().isoformat()}
            if error_msg:
                reg[key]["error"] = error_msg[:200]
            # Purger les entrées vieilles de plus de 48h
            cutoff = (datetime.now() - timedelta(hours=48)).isoformat()
            reg = {k: v for k, v in reg.items() if v.get("ts", "") > cutoff}
            state["_idempotence_registry"] = reg
            _save_state(state)
        except Exception as e:
            logger.debug(f"Idempotence save error: {e}")

    async def run_task(self, task: ScheduledTask) -> bool:
        """
        Exécute une tâche.
        
        Returns:
            True si succès, False sinon
        """
        if not task.enabled:
            return False
        
        handler = self.handlers.get(task.handler_name)
        if not handler:
            logger.error(f"Handler non trouvé: {task.handler_name}")
            return False

        # ── Idempotence check (P4.3) ──
        cron_window = self._compute_cron_window(task)
        task_key = self._compute_task_key(task, cron_window)
        registry = self._load_idempotence_registry()
        entry = registry.get(task_key)
        if entry:
            if entry["status"] == "SUCCESS":
                logger.debug(f"Idempotent skip: {task.handler_name} ({task_key})")
                task.status = TaskStatus.COMPLETED
                if task.frequency != TaskFrequency.ONCE:
                    task.calculate_next_run()
                    task.status = TaskStatus.PENDING
                return True
            if entry["status"] == "RUNNING":
                # Si RUNNING depuis plus de 2× le timeout → considérer comme crash, re-run
                try:
                    entry_ts = datetime.fromisoformat(entry["ts"])
                    if (datetime.now() - entry_ts).total_seconds() < 2 * task.timeout_seconds:
                        logger.debug(f"Idempotent skip (still running): {task_key}")
                        return False
                except (ValueError, KeyError):
                    pass  # timestamp invalide ou manquant, on laisse re-run
            # FAILURE or stale RUNNING → re-run
        self._save_idempotence_entry(task_key, "RUNNING")

        task.status = TaskStatus.RUNNING
        task.run_count += 1
        started = perf_counter()
        
        try:
            # Exécuter avec timeout
            raw_result = await asyncio.wait_for(
                handler(),
                timeout=task.timeout_seconds
            )

            duration_ms = (perf_counter() - started) * 1000.0
            result = self._normalize_handler_result(
                task,
                raw_result,
                duration_ms,
                fallback_status="ok",
            )
            task.metadata["last_result"] = result
            task.metadata["last_duration_ms"] = result.get("duration_ms")
            task.metadata["last_reason"] = result.get("reason")

            success = bool(result.get("success", True))
            if not success:
                self._save_idempotence_entry(task_key, "FAILURE", error_msg=result.get("reason", ""))
                task.fail_count += 1
                task.status = TaskStatus.FAILED
                task.last_run = datetime.now()
                if task.frequency != TaskFrequency.ONCE:
                    task.calculate_next_run()
                    task.status = TaskStatus.PENDING
                # Backoff override APRÈS calculate_next_run (sinon écrasé)
                if task.fail_count >= 10:
                    task.enabled = False
                    logger.error(f"Tâche {task.name} désactivée après 10 échecs consécutifs")
                elif task.fail_count == 5:
                    task.next_run = datetime.now() + timedelta(hours=2)
                    logger.warning(f"Tâche {task.name} en backoff 2h après 5 échecs")
                logger.warning(
                    "Tâche {} renvoie success=False (status={}, reason={})",
                    task.name,
                    result.get("status"),
                    result.get("reason"),
                )
                return False
            
            task.status = TaskStatus.COMPLETED
            task.success_count += 1
            task.last_run = datetime.now()
            
            # Planifier la prochaine exécution
            if task.frequency != TaskFrequency.ONCE:
                task.calculate_next_run()
                task.status = TaskStatus.PENDING
            
            self._save_idempotence_entry(task_key, "SUCCESS")
            logger.debug(f"Tâche réussie: {task.name}")
            return True
            
        except asyncio.TimeoutError:
            duration_ms = (perf_counter() - started) * 1000.0
            result = self._normalize_handler_result(
                task,
                {"error": f"timeout after {task.timeout_seconds}s"},
                duration_ms,
                fallback_status="timeout",
                fallback_reason="timeout",
            )
            task.metadata["last_result"] = result
            task.metadata["last_duration_ms"] = result.get("duration_ms")
            task.metadata["last_reason"] = result.get("reason")
            self._save_idempotence_entry(task_key, "FAILURE", error_msg=f"timeout after {task.timeout_seconds}s")
            logger.error(f"Timeout tâche: {task.name}")
            task.fail_count += 1
            task.status = TaskStatus.FAILED
            task.last_run = datetime.now()
            if task.frequency != TaskFrequency.ONCE:
                task.calculate_next_run()
                task.status = TaskStatus.PENDING
            if task.fail_count >= 10:
                task.enabled = False
                logger.error(f"Tâche {task.name} désactivée après 10 échecs consécutifs")
            elif task.fail_count == 5:
                task.next_run = datetime.now() + timedelta(hours=2)
                logger.warning(f"Tâche {task.name} en backoff 2h après 5 échecs")
            return False
            
        except Exception as e:
            duration_ms = (perf_counter() - started) * 1000.0
            result = self._normalize_handler_result(
                task,
                {"error": str(e)},
                duration_ms,
                fallback_status="error",
                fallback_reason="exception",
            )
            task.metadata["last_result"] = result
            task.metadata["last_duration_ms"] = result.get("duration_ms")
            task.metadata["last_reason"] = result.get("reason")
            self._save_idempotence_entry(task_key, "FAILURE", error_msg=str(e))
            logger.error(f"Erreur tâche {task.name}: {e}")
            task.fail_count += 1
            task.status = TaskStatus.FAILED
            task.last_run = datetime.now()
            if task.frequency != TaskFrequency.ONCE:
                task.calculate_next_run()
                task.status = TaskStatus.PENDING
            if task.fail_count >= 10:
                task.enabled = False
                logger.error(f"Tâche {task.name} désactivée après 10 échecs consécutifs")
            elif task.fail_count == 5:
                task.next_run = datetime.now() + timedelta(hours=2)
                logger.warning(f"Tâche {task.name} en backoff 2h après 5 échecs")
            return False
    
    async def _main_loop(self):
        """Boucle principale du scheduler."""
        logger.info("🔄 Boucle du scheduler démarrée")
        
        while self._running:
            now = datetime.now()
            
            # Trouver les tâches à exécuter
            due_tasks = [
                t for t in self.tasks.values()
                if t.enabled and t.status == TaskStatus.PENDING and t.next_run <= now
            ]
            
            # Exécuter les tâches — priorité absolue en premier, AVANT le cut anti-backlog
            # BUG FIX: le tri priorité doit précéder le cut pour que runtime_health/provider_probe
            # ne soient jamais sacrifiés au profit de tâches moins importantes.
            priority_order = ["runtime_health", "provider_probe", "save_state_real"]
            due_tasks.sort(key=lambda t: (
                priority_order.index(t.handler_name) if t.handler_name in priority_order else 100,
                t.next_run  # secondaire : la plus ancienne d'abord dans chaque niveau
            ))

            # FIX-D : déduplication par handler_name (une seule instance par handler par cycle)
            _seen_handlers: set[str] = set()
            due_tasks = [
                t for t in due_tasks
                if t.handler_name not in _seen_handlers
                and not _seen_handlers.add(t.handler_name)  # type: ignore[func-returns-value]
            ]

            # Anti-backlog : si trop de tâches en retard, defer les MOINS prioritaires
            # Important : PAS de calculate_next_run() → elles restent dues et s'exécutent
            # dans le prochain cycle (10 s), sans attendre tout leur intervalle.
            if len(due_tasks) > 5:
                skipped = due_tasks[5:]
                due_tasks = due_tasks[:5]
                # Les tâches restent en status PENDING avec leur next_run inchangé
                # → reprises au prochain tick du scheduler (10 s)
                logger.warning(f"⚠️ Backlog scheduler: {len(skipped)} tâches déférées au prochain cycle (anti-emballement)")

            # Pause des tâches quand l'agent ReAct est actif
            # Seul save_state_real (écriture JSON légère) est autorisé ;
            # provider_probe (appels LLM tous providers) et runtime_health (I/O)
            # entrent en contention avec l'agent et bloquent le event loop Uvicorn.
            if _AGENT_BUSY:
                _AGENT_BUSY_ALLOWED = {"save_state_real", "save_state"}
                n_before = len(due_tasks)
                due_tasks = [t for t in due_tasks if t.handler_name in _AGENT_BUSY_ALLOWED]
                n_paused = n_before - len(due_tasks)
                if n_paused:
                    self._busy_log_n = getattr(self, "_busy_log_n", 0) + 1
                    if self._busy_log_n == 1 or self._busy_log_n % 6 == 0:
                        logger.debug(f"⏸️ Agent occupé: {n_paused} tâche(s) en pause (cycle #{self._busy_log_n}, seul save_state autorisé)")
                else:
                    self._busy_log_n = 0

            # P3.3: Exécution parallèle des tâches non-critiques
            _CRITICAL_HANDLERS = {"runtime_health", "provider_probe", "save_state_real", "save_state"}
            # P5.2: tâches lourdes lancées en background (fire-and-forget)
            _HEAVY_HANDLERS = {
                "daily_github_project", "micro_eval_full", "judge_pipeline",
                "rejection_sampling_light", "backup_rollback_test", "daily_skill_autonomy",
            }
            critical_tasks = [t for t in due_tasks if t.handler_name in _CRITICAL_HANDLERS]
            heavy_tasks = [t for t in due_tasks if t.handler_name in _HEAVY_HANDLERS and t.handler_name not in _CRITICAL_HANDLERS]
            parallel_tasks = [t for t in due_tasks if t.handler_name not in _CRITICAL_HANDLERS and t.handler_name not in _HEAVY_HANDLERS]

            # Critiques: séquentielles (état partagé, ordre important)
            for task in critical_tasks:
                await self.run_task(task)

            # Lourdes: fire-and-forget (P5.2 — ne bloquent pas le scheduler)
            for task in heavy_tasks:
                asyncio.create_task(self.run_task(task))
                logger.debug(f"🚀 Tâche lourde lancée en background: {task.name}")

            # Non-critiques: parallèles (gather avec return_exceptions)
            if parallel_tasks:
                await asyncio.gather(
                    *(self.run_task(t) for t in parallel_tasks),
                    return_exceptions=True,
                )

            # Nettoyage automatique des tâches ONCE terminées/annulées depuis plus d'1h
            _cleanup_cutoff = now - timedelta(hours=1)
            _to_remove = [
                tid for tid, t in self.tasks.items()
                if t.frequency == TaskFrequency.ONCE
                and t.status in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
                and t.last_run is not None
                and t.last_run < _cleanup_cutoff
            ]
            for tid in _to_remove:
                del self.tasks[tid]
            if _to_remove:
                logger.debug(f"🧹 Scheduler: {len(_to_remove)} tâche(s) ONCE nettoyée(s)")

            # Attendre avant la prochaine vérification
            await asyncio.sleep(10)  # Check toutes les 10 secondes
    
    async def start(self):
        """Démarre le scheduler."""
        if self._running:
            return
        
        self._running = True
        self._loop_task = asyncio.create_task(self._main_loop())
        
        logger.info("⏰ Scheduler démarré")
    
    async def stop(self):
        """Arrête le scheduler."""
        self._running = False
        
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
        
        logger.info("⏰ Scheduler arrêté")
    
    def get_pending_tasks(self) -> List[ScheduledTask]:
        """Retourne les tâches en attente."""
        return [t for t in self.tasks.values() if t.status == TaskStatus.PENDING and t.enabled]

    def get_overdue_tasks(self) -> List[ScheduledTask]:
        """Retourne uniquement les tâches en retard (next_run dépassé)."""
        now = datetime.now()
        return [t for t in self.tasks.values()
                if t.status == TaskStatus.PENDING and t.enabled and t.next_run < now]

    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques du scheduler."""
        total = len(self.tasks)
        pending = len(self.get_pending_tasks())
        overdue = len(self.get_overdue_tasks())
        
        total_runs = sum(t.run_count for t in self.tasks.values())
        total_success = sum(t.success_count for t in self.tasks.values())
        
        return {
            "total_tasks": total,
            "pending": pending,
            "overdue": overdue,
            "total_runs": total_runs,
            "total_success": total_success,
            "success_rate": (total_success / total_runs * 100) if total_runs > 0 else 0,
            "running": self._running,
        }
    
    def setup_default_tasks(self):
        """Configure les tâches par défaut pour LUMENA (profil production continue)."""
        
        # Import local de HANDLER_TIMEOUTS (défini dans ops_handlers)
        try:
            from .ops_handlers import HANDLER_TIMEOUTS
        except ImportError:
            HANDLER_TIMEOUTS = {}
        
        # ── TÂCHES EXISTANTES (conservées) ──

        # NOTE: curiosity_update, daily_skill_autonomy, daily_code_analysis
        # supprimés (13/04/2026) — curiosity polling inutile, skill autonomy
        # créait des skills poubelle, code analysis loggait sans agir.

        # Archivage workspace quotidien (04h chaque jour)
        try:
            t = self.schedule(
                name="Workspace Archive",
                description="Archive les vieux projets (>30j) dans workspace/_archives/",
                handler_name="workspace_archive",
                frequency=TaskFrequency.CRON,
                cron_expr="0 4 * * *",
            )
            t.timeout_seconds = HANDLER_TIMEOUTS.get("workspace_archive", 300)
            logger.info("⏰ Archivage workspace planifié (04h quotidien)")
        except Exception as e:
            logger.debug(f"Workspace Archive non planifié : {e}")

        # Pipeline d'auto-amélioration hebdomadaire (dimanche à 3h du matin)
        try:
            weekly_task = self.schedule(
                name="Weekly Auto-Improve",
                description="Juge les nouvelles données, rejection sampling, re-train et déploie si amélioré",
                handler_name="weekly_auto_improve",
                frequency=TaskFrequency.CRON,
                cron_expr="0 3 * * 0",  # Dimanche 03:00
            )
            weekly_task.timeout_seconds = 14400  # 4h max (aligne train+export+benchmark)
            logger.info("⏰ Pipeline auto-amélioration hebdomadaire planifié (dim. 3h)")
        except Exception as e:
            logger.debug(f"Pipeline auto-amélioration non planifié (croniter manquant ?) : {e}")

        # ── TÂCHES OPS PRODUCTION CONTINUE ──

        self._setup_ops_tasks()

        logger.info("✅ Tâches par défaut configurées (profil production continue)")

    def _setup_ops_tasks(self):
        """Configure les tâches ops de production continue."""
        try:
            from .ops_handlers import HANDLER_TIMEOUTS
        except ImportError:
            logger.warning("⚠️ ops_handlers non disponible, tâches ops non planifiées")
            return

        # ── Toutes les 45 min : Runtime Health ──
        t = self.schedule(
            name="Runtime Health",
            description="Vérifie RAM, disque, locks stale, queue scheduler",
            handler_name="runtime_health",
            frequency=TaskFrequency.EVERY_45_MINUTES,
        )
        t.timeout_seconds = HANDLER_TIMEOUTS.get("runtime_health", 30)

        # ── Toutes les 15 min : Provider Probe ──
        t = self.schedule(
            name="Provider Probe",
            description="Sonde les providers LLM (observation uniquement)",
            handler_name="provider_probe",
            frequency=TaskFrequency.EVERY_15_MINUTES,
        )
        t.timeout_seconds = HANDLER_TIMEOUTS.get("provider_probe", 60)

        # ── Toutes les 30 min : Data Ingest Delta ──
        t = self.schedule(
            name="Data Ingest Delta",
            description="Analyse incrémentale du training pool",
            handler_name="data_ingest_delta",
            frequency=TaskFrequency.INTERVAL_MS,
            interval_ms=30 * 60 * 1000,  # 30 minutes
        )
        t.timeout_seconds = HANDLER_TIMEOUTS.get("data_ingest_delta", 60)

        # ── Toutes les 30 min : Memory Hygiene ──
        t = self.schedule(
            name="Memory Hygiene",
            description="Dédup ChromaDB, marquage low-quality (dry_run par défaut)",
            handler_name="memory_hygiene",
            frequency=TaskFrequency.INTERVAL_MS,
            interval_ms=30 * 60 * 1000,  # 30 minutes
        )
        t.timeout_seconds = HANDLER_TIMEOUTS.get("memory_hygiene", 120)

        # ── Toutes les heures : Micro Eval Light (3 prompts rule-based) ──
        t = self.schedule(
            name="Micro Eval Light",
            description="Évaluation rapide sur 3 prompts fixes (rule-based)",
            handler_name="micro_eval_light",
            frequency=TaskFrequency.EVERY_HOUR,
        )
        t.timeout_seconds = HANDLER_TIMEOUTS.get("micro_eval_light", 180)

        # ── Toutes les 15 min : Save State réel ──
        t = self.schedule(
            name="Save State",
            description="Sauvegarde l'état ops + scheduler",
            handler_name="save_state_real",
            frequency=TaskFrequency.EVERY_15_MINUTES,
        )
        t.timeout_seconds = 30

        # ── Toutes les 2h : Learning Curation ──
        t = self.schedule(
            name="Learning Curation",
            description="Pré-filtre training_pool vers candidats validated",
            handler_name="learning_curation",
            frequency=TaskFrequency.INTERVAL_MS,
            interval_ms=2 * 60 * 60 * 1000,  # 2 heures
        )
        t.timeout_seconds = HANDLER_TIMEOUTS.get("learning_curation", 300)

        # ── CRON nocturnes (nécessitent croniter) ──
        try:
            # 01:00 chaque jour : Micro Eval Full (20 prompts)
            t = self.schedule(
                name="Micro Eval Full",
                description="Évaluation complète nocturne sur 20 prompts + tendance 7j",
                handler_name="micro_eval_full",
                frequency=TaskFrequency.CRON,
                cron_expr="0 1 * * *",
            )
            t.timeout_seconds = HANDLER_TIMEOUTS.get("micro_eval_full", 600)

            # 02:00 chaque jour : Judge Pipeline
            t = self.schedule(
                name="Judge Pipeline",
                description="Juge la qualité des conversations récentes (skip si retrain_lock)",
                handler_name="judge_pipeline",
                frequency=TaskFrequency.CRON,
                cron_expr="0 2 * * *",
            )
            t.timeout_seconds = HANDLER_TIMEOUTS.get("judge_pipeline", 7200)

            # 03:00 lun-sam : Rejection Sampling Light
            t = self.schedule(
                name="Rejection Sampling Light",
                description="Rejection sampling quota faible (skip dimanche + retrain_lock)",
                handler_name="rejection_sampling_light",
                frequency=TaskFrequency.CRON,
                cron_expr="0 3 * * 1-6",  # Lun-Sam seulement
            )
            t.timeout_seconds = HANDLER_TIMEOUTS.get("rejection_sampling_light", 7200)

            # 04:00 lun-sam : Retrain Readiness Check
            t = self.schedule(
                name="Retrain Readiness",
                description="Go/no-go pour le retrain hebdomadaire",
                handler_name="retrain_readiness",
                frequency=TaskFrequency.CRON,
                cron_expr="0 4 * * 1-6",  # Lun-Sam seulement
            )
            t.timeout_seconds = HANDLER_TIMEOUTS.get("retrain_readiness", 60)

            # 23:55 chaque jour : Rapport Quotidien
            t = self.schedule(
                name="Daily Report",
                description="Génère le rapport quotidien complet dans data/reports/",
                handler_name="daily_report",
                frequency=TaskFrequency.CRON,
                cron_expr="55 23 * * *",
            )
            t.timeout_seconds = HANDLER_TIMEOUTS.get("daily_report", 120)

            # Dimanche 05:00 : Backup + Rollback Test
            t = self.schedule(
                name="Backup Rollback Test",
                description="Backup hebdomadaire + test de restauration",
                handler_name="backup_rollback_test",
                frequency=TaskFrequency.CRON,
                cron_expr="0 5 * * 0",  # Dimanche 05:00
            )
            t.timeout_seconds = HANDLER_TIMEOUTS.get("backup_rollback_test", 600)

            logger.info("⏰ Tâches CRON ops planifiées (eval, judge, rejection, report, backup)")

        except Exception as e:
            logger.debug(f"Tâches CRON ops non planifiées (croniter manquant ?) : {e}")


# Instance singleton avec lock thread-safe (Phase 2.1)
import threading
_scheduler: Optional[LumenaScheduler] = None
_scheduler_lock = threading.Lock()


def get_scheduler(data_dir: Optional[Path] = None) -> LumenaScheduler:
    """Obtient l'instance singleton du scheduler (thread-safe)."""
    global _scheduler
    
    # Double-check locking pattern
    if _scheduler is None:
        with _scheduler_lock:
            if _scheduler is None:
                _scheduler = LumenaScheduler(data_dir)
    return _scheduler
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
