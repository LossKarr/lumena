"""Validation syntaxique légère pour les fichiers édités par CodeAgent et ReAct.

Appelée depuis (vérifié le 2026-09-02) :
- src/reasoning/handlers/files.py (ReAct write_file/edit_file/apply_patch)
- src/llm/codex_codeagent.py (porte de validation du CodeAgent Codex)

`src/agents/sub_agent.py` ne passe PAS par ici : il a son propre mécanisme
(`_syntax_clean_snapshot`). La docstring d'origine l'annonçait comme appelant.

Deux entrées :
- `verify_syntax()` → `(verdict, detail)`, verdict ∈ {`ok`, `erreur`,
  `non_verifiable`}. **À préférer** : c'est la seule qui distingue « validé » de
  « pas pu vérifier ».
- `check_syntax()` → `str` ("" si rien à signaler). Contrat historique, conservé.
  ⚠️ Il rend "" AUSSI pour un fichier non vérifiable — c'est sa limite d'origine.

Tous les checks sont :
- Async (subprocess + to_thread)
- Bornés : timeout court (5-15s)
- Non-bloquants : ne lèvent jamais d'exception
- Sortie brève : tronquée à 600 caractères max
- LOT 8a — **l'absence d'outil n'est plus un succès** : elle rend
  `non_verifiable`, avec la raison.
"""
from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from loguru import logger


_PY_EXTS = (".py",)
_JS_EXTS = (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx")
_HTML_EXTS = (".html", ".htm")
_CSS_EXTS = (".css", ".scss", ".sass")
_JSON_EXTS = (".json",)

# ── LOT 8a (2026-09-02) — DEUX ÉTATS POUR TROIS SITUATIONS ───────────────────────
#
# `check_syntax` rendait `""` pour « validé » ET pour « je n'ai pas pu vérifier » :
# extension non couverte, outil absent, exception silencieuse. L'appelant ne pouvait
# donc pas distinguer un fichier PROUVÉ correct d'un fichier JAMAIS REGARDÉ.
#
# `_check_javascript` l'assumait même dans sa docstring — « silencieux si node
# absent » — ce qui rendait un `.js` « bon » sur une machine sans node.
#
# Mesuré sur `workspace/` (hors node_modules, .git, venv) : **85 fichiers de code
# source hors validation sur 2 535**, dont **61 PHP**. C'est exactement le lot Z38 —
# « le CodeAgent corrigeait du PHP sans interpréteur PHP : ~20 fichiers écrits, 0
# validé, redéclarations réparées de tête 6× » — et il n'a jamais été refermé.
#
# La discipline injectée dans chaque worker de code ordonne pourtant : « après CHAQUE
# mutation significative, EXÉCUTE avant de conclure ». En Rust, Go, Java ou PHP, cet
# ordre est IMPOSSIBLE à exécuter. Le correctif n'est pas d'installer des
# compilateurs (c'est le lot 8b/8c, il demande une décision) : c'est que l'agent
# SACHE qu'il est aveugle, et le DISE.
#
# Même correctif que Z40c, où la Verification Gate rendait `passed=True` sans rien
# valider : un TROISIÈME état.

OK = "ok"
ERREUR = "erreur"
NON_VERIFIABLE = "non_verifiable"


def _sans_outil(nom: str, ext: str) -> tuple:
    return (NON_VERIFIABLE,
            f"{ext} : `{nom}` introuvable sur cette machine — fichier NON vérifié")


# ── LOT 8bc (2026-09-03) — CHERCHER AVANT DE CONCLURE ────────────────────────────
#
# Décision utilisateur : « il faudra les deux — test en local si docker non
# disponible, et docker si disponible ; il faut toujours qu'elle fasse ».
#
# Ce que le run du 02/09 a montré, et qui rend cette règle nécessaire : la mission a
# cherché `where php` (PATH seulement), sa commande PowerShell de recherche a PLANTÉ
# (`out-file : FileStream…`), Docker était arrêté, et elle a conclu **« PHP n'est pas
# installé sur cette machine »**. Six minutes plus tard elle trouvait
# `C:\php\php.exe` en trois itérations. **L'interpréteur était là.**
#
# Le lot 8a distinguait « validé » de « pas pu vérifier ». Celui-ci va un cran plus
# loin : « pas pu vérifier » ne doit plus vouloir dire « je n'ai pas su chercher ».
#
# Cible mesurée sur `workspace/` (hors node_modules, .git, venv, .backups) :
#
#     87 fichiers de code non validables — 63 .php (72 %), 11 .sql, 8 .cs, 4 .lua, 1 .ps1
#
# PHP est donc l'essentiel du gisement.

#: `extension → (outil, arguments de vérification, image Docker | "")`.
#: L'outil doit valider UN fichier isolé et sortir non-zéro sur erreur de syntaxe.
#: `.sql`, `.cs`, `.rs`, `.java` n'y figurent pas : ils demandent un projet complet,
#: pas un fichier — les déclarer ici mentirait sur ce qu'on vérifie.
_VALIDATEURS = {
    ".php": ("php", ["-l"], "php:8.3-cli"),
    ".rb": ("ruby", ["-c"], "ruby:3.3-slim"),
    ".lua": ("luac", ["-p"], ""),
    ".sh": ("bash", ["-n"], "bash:5"),
    ".bash": ("bash", ["-n"], "bash:5"),
    ".go": ("gofmt", ["-e"], "golang:1.23-alpine"),
    ".pl": ("perl", ["-c"], "perl:5.40-slim"),
}

#: Où chercher un outil ABSENT du PATH. C'est le cœur du lot : `C:\\php\\php.exe`
#: existait et n'a pas été trouvé parce que personne n'a regardé ailleurs que dans
#: le PATH.
_CHEMINS_COURANTS = {
    "php": (
        r"C:\php\php.exe", r"C:\xampp\php\php.exe", r"C:\laragon\bin\php\php.exe",
        r"C:\wamp64\bin\php\php.exe", r"C:\wamp\bin\php\php.exe",
        r"C:\tools\php\php.exe", "/usr/bin/php", "/usr/local/bin/php",
    ),
    "ruby": (r"C:\Ruby34-x64\bin\ruby.exe", r"C:\Ruby33-x64\bin\ruby.exe",
             r"C:\tools\ruby\bin\ruby.exe", "/usr/bin/ruby"),
    "luac": (r"C:\Program Files\Lua\luac.exe", r"C:\lua\luac.exe", "/usr/bin/luac"),
    "gofmt": (r"C:\Go\bin\gofmt.exe", r"C:\Program Files\Go\bin\gofmt.exe",
              "/usr/local/go/bin/gofmt"),
    "bash": (r"C:\Program Files\Git\bin\bash.exe", "/bin/bash", "/usr/bin/bash"),
    "perl": (r"C:\Strawberry\perl\bin\perl.exe", "/usr/bin/perl"),
}

#: Caches de module. Chercher un exécutable sur le disque à CHAQUE fichier écrit
#: coûterait plus cher que la vérification elle-même.
_cache_outils: dict = {}
_cache_docker: list = []


def _est_lanceur_wsl_bash(nom: str, chemin: str) -> bool:
    """Le lanceur WSL Windows n'accepte pas directement nos chemins locaux."""
    if nom != "bash" or not chemin:
        return False
    normalise = chemin.replace("/", "\\").casefold()
    return (
        normalise.endswith(r"\windows\system32\bash.exe")
        or r"\microsoft\windowsapps\bash.exe" in normalise
    )


def trouver_outil(nom: str) -> str:
    """Chemin de l'outil, "" s'il est introuvable. PATH d'abord, puis les
    emplacements courants — c'est cette seconde étape qui manquait."""
    if nom in _cache_outils:
        return _cache_outils[nom]
    chemin = shutil.which(nom) or ""
    if _est_lanceur_wsl_bash(nom, chemin):
        chemin = ""
    if not chemin:
        for candidat in _CHEMINS_COURANTS.get(nom, ()):
            try:
                if Path(candidat).is_file():
                    chemin = candidat
                    break
            except Exception:
                continue
    _cache_outils[nom] = chemin
    return chemin


def docker_disponible() -> bool:
    """Le DAEMON répond-il ? `docker --version` ne suffit pas : au run du 02/09 le
    binaire existait et le daemon était arrêté — c'est `docker info` qui tranche."""
    if _cache_docker:
        return _cache_docker[0]
    ok = False
    try:
        if shutil.which("docker"):
            proc = subprocess.run(
                ["docker", "info", "--format", "{{.ServerVersion}}"],
                capture_output=True, text=True, timeout=12,
            )
            ok = proc.returncode == 0 and bool((proc.stdout or "").strip())
    except Exception:
        ok = False
    _cache_docker.append(ok)
    return ok


def reset_caches_pour_tests() -> None:
    """Les caches sont des états de module : les tests doivent pouvoir les vider."""
    _cache_outils.clear()
    _cache_docker.clear()


async def _lancer(cmd: list, cwd=None, timeout: float = 20.0) -> tuple:
    """`(returncode, sortie)`. Ne lève jamais — `(None, raison)` si l'exécution
    elle-même a échoué."""
    try:
        proc = await asyncio.to_thread(
            subprocess.run, cmd, capture_output=True, text=True,
            timeout=timeout, cwd=str(cwd) if cwd else None,
        )
        return proc.returncode, ((proc.stderr or "") + (proc.stdout or "")).strip()
    except subprocess.TimeoutExpired:
        return None, f"délai dépassé ({timeout:.0f}s)"
    except Exception as exc:
        return None, str(exc)[:200]


async def _verifier_par_table(path: Path, ext: str) -> tuple:
    """LOT 8bc — les DEUX voies, dans l'ordre décidé : local, puis Docker.

    `non_verifiable` n'est rendu qu'après avoir tenté les deux — et il DIT
    lesquelles ont été tentées. C'est toute la différence avec le run du 02/09,
    où « PHP n'est pas installé » était faux.
    """
    outil, args, image = _VALIDATEURS[ext]

    # ── voie 1 : l'outil, en local (PATH puis emplacements courants) ─────────
    exe = trouver_outil(outil)
    if exe:
        code, sortie = await _lancer([exe, *args, str(path)], cwd=path.parent)
        if code == 0:
            return (OK, "")
        if code is not None:
            return (ERREUR, (sortie or f"{outil} a rejeté le fichier")[:500])
        # L'outil existe mais n'a pas pu s'exécuter : ce n'est ni bon ni mauvais.
        return (NON_VERIFIABLE, f"{ext} : `{outil}` trouvé mais inexécutable — {sortie}")

    # ── voie 2 : Docker ──────────────────────────────────────────────────────
    #
    # COÛT, mesuré : une vérification locale prend 60-90 ms (comparable aux 37 ms
    # de ruff sur un `.py`). La première recherche d'un outil absent coûte ~450 ms
    # — sondage disque + `docker info` — puis 0,2 ms grâce aux caches de module.
    #
    # Le premier `docker run` d'une image absente doit la TÉLÉCHARGER : d'où le
    # timeout large. Cela n'arrive qu'une fois (Docker garde l'image), et seulement
    # si l'outil manque en local ET que le daemon répond. C'est le prix de la règle
    # « il faut toujours qu'elle fasse » — assumé, pas subi.
    if image and docker_disponible():
        code, sortie = await _lancer(
            ["docker", "run", "--rm", "--network", "none",
             "-v", f"{path.parent}:/w", "-w", "/w",
             image, outil, *args, path.name],
            timeout=180.0,   # la première fois, l'image doit être téléchargée
        )
        if code == 0:
            return (OK, "")
        if code is not None:
            return (ERREUR, (sortie or f"{outil} a rejeté le fichier")[:500])
        return (NON_VERIFIABLE,
                f"{ext} : ni `{outil}` en local, ni Docker n'a abouti — {sortie}")

    # ── les deux voies ont été tentées, et elles ont échoué ──────────────────
    pourquoi = "Docker indisponible" if image else "pas d'image Docker pour ce format"
    return (NON_VERIFIABLE,
            f"{ext} : `{outil}` introuvable (PATH et emplacements courants) et "
            f"{pourquoi} — fichier NON vérifié")


async def verify_syntax(
    file_path: str | Path, *, workspace_root: Path | None = None
) -> tuple:
    """`(verdict, detail)` — verdict ∈ {`ok`, `erreur`, `non_verifiable`}.

    C'est la fonction à appeler quand on veut savoir si le fichier a RÉELLEMENT été
    regardé. `check_syntax` reste l'adaptateur historique (une chaîne), pour ne rien
    casser chez ses appelants.

    Ne lève jamais d'exception.
    """
    try:
        from src.config.codeagent_flags import REACT_QUALITY_GATES
        if not REACT_QUALITY_GATES:
            # Choix de l'exploitant, pas une incapacité : ne pas alarmer.
            return (OK, "")
    except Exception:
        pass  # flag inaccessible → on continue quand même

    try:
        path = Path(file_path)
        if not path.exists() or not path.is_file():
            return (NON_VERIFIABLE, "fichier absent au moment de la vérification")
        suffix = path.suffix.lower()
        if suffix in _PY_EXTS:
            return await _check_python(path, workspace_root)
        if suffix in _JS_EXTS:
            return await _check_javascript(path)
        if suffix in _JSON_EXTS:
            return _check_json(path)
        if suffix in _HTML_EXTS:
            return _check_brackets(path, html_mode=True)
        if suffix in _CSS_EXTS:
            return _check_brackets(path, html_mode=False)
        # LOT 8bc — les langages à validateur externe : local d'abord, Docker
        # ensuite, et jamais de verdict avant d'avoir tenté les deux.
        if suffix in _VALIDATEURS:
            return await _verifier_par_table(path, suffix)
        return (NON_VERIFIABLE,
                f"{suffix or 'sans extension'} : aucun validateur pour ce format")
    except Exception as exc:
        logger.debug(f"[syntax_check] exception silencieuse: {exc}")
        return (NON_VERIFIABLE, "la vérification a échoué (erreur interne)")


async def check_syntax(file_path: str | Path, *, workspace_root: Path | None = None) -> str:
    """Dispatcher historique. Retourne "" si OK **ou non vérifiable**, le message
    d'erreur sinon.

    ⚠️ Cette fonction ne peut PAS distinguer « validé » de « pas pu vérifier » —
    c'est sa limite d'origine, conservée pour ses appelants. Utilise `verify_syntax`
    dès que la distinction compte.
    """
    verdict, detail = await verify_syntax(file_path, workspace_root=workspace_root)
    return detail if verdict == ERREUR else ""


async def _check_python(path: Path, workspace_root: Path | None) -> tuple:
    """Ruff > py_compile (syntaxe + lint léger)."""
    root = workspace_root or Path(__file__).resolve().parent.parent.parent

    # 1) ruff (priorité)
    ruff_exe = root / "venv" / "Scripts" / "ruff.exe"
    if not ruff_exe.exists():
        ruff_exe = root / "venv" / "bin" / "ruff"
    if not ruff_exe.exists():
        ruff_path = shutil.which("ruff")
        if ruff_path:
            ruff_exe = Path(ruff_path)

    if ruff_exe.exists():
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                [
                    str(ruff_exe), "check", "--select", "E,F",
                    "--no-fix", "--output-format", "concise", str(path),
                ],
                capture_output=True, text=True, timeout=15,
            )
            if proc.returncode != 0 and (proc.stdout or "").strip():
                return (ERREUR, proc.stdout.strip()[:600])
            return (OK, "")
        except Exception:
            pass  # fallback py_compile

    # 2) py_compile (syntaxe stricte uniquement)
    try:
        import sys
        proc = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "py_compile", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return (ERREUR, (proc.stderr or proc.stdout).strip()[:500])
        return (OK, "")
    except Exception:
        # LOT 8a — ruff ET py_compile hors service : le fichier n'a PAS été regardé.
        # L'ancien `return ""` le déclarait bon.
        return (NON_VERIFIABLE, ".py : ni ruff ni py_compile n'ont pu s'exécuter")


def verifier_js_par_node(path: Path) -> tuple:
    """`node --check` sur un fichier — **synchrone**, pour les appelants qui le sont.

    GATE-2 (24/09/2026) : extrait de `_check_javascript` sans rien changer a son
    comportement. Le gate de `code_validator` est synchrone et comptait les
    parentheses a la main alors que CE verificateur existait deja : il etait
    branche sur `files.py` et `codex_codeagent.py`, pas sur le gate. Une seule
    source de verite pour « node a-t-il accepte ce fichier ».
    """
    node_exe = shutil.which("node")
    if not node_exe:
        return _sans_outil("node", path.suffix.lower() or ".js")
    try:
        proc = subprocess.run(
            [node_exe, "--check", str(path)],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            return (ERREUR, (proc.stderr or proc.stdout).strip()[:500])
        return (OK, "")
    except Exception:
        return (NON_VERIFIABLE, "node --check n'a pas pu s'exécuter (timeout ?)")


async def _check_javascript(path: Path) -> tuple:
    """`node --check`. LOT 8a — node absent ne veut plus dire « correct »."""
    try:
        return await asyncio.to_thread(verifier_js_par_node, path)
    except Exception:
        return (NON_VERIFIABLE, "node --check n'a pas pu s'exécuter (timeout ?)")


def _check_json(path: Path) -> tuple:
    """json.loads — détection rapide JSON cassé."""
    import json
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return (OK, "")
    except json.JSONDecodeError as exc:
        return (ERREUR, f"JSON invalide L{exc.lineno}:{exc.colno} — {exc.msg}"[:300])
    except Exception:
        # LOT 8a — illisible (encodage, droits) : ce n'est pas « valide ».
        return (NON_VERIFIABLE, ".json : fichier illisible")


def _check_brackets(path: Path, *, html_mode: bool) -> tuple:
    """Bracket balance check (HTML/CSS).

    Ignore strings/commentaires basiques. Détecte juste les déséquilibres
    grossiers ({} sans paire, ou tags HTML grossièrement déséquilibrés).
    """
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return (NON_VERIFIABLE, "fichier illisible")

    # Strip strings + comments
    import re
    if html_mode:
        # Strip <!-- ... -->
        clean = re.sub(r"<!--[\s\S]*?-->", "", content)
        # Compte les balises ouvrantes vs fermantes (heuristique grossière)
        opens = len(re.findall(r"<(?!/)[a-zA-Z][^>]*?(?<!/)>", clean))
        closes = len(re.findall(r"</[a-zA-Z][^>]*?>", clean))
        # Tolérance large : tags void (img, br, input, hr, meta, link)
        # Si écart > 10 → probable problème
        if abs(opens - closes) > 15:
            return (ERREUR,
                    f"HTML déséquilibré : {opens} balises ouvrantes vs {closes} fermantes")
    else:
        # CSS : strip /* ... */ et strings
        clean = re.sub(r'/\*[\s\S]*?\*/', "", content)
        clean = re.sub(r'"(?:[^"\\]|\\.)*"', '""', clean)
        clean = re.sub(r"'(?:[^'\\]|\\.)*'", "''", clean)
        diff = clean.count("{") - clean.count("}")
        if diff != 0:
            return (ERREUR, f"CSS accolades déséquilibrées : {diff:+d} (excès)")
    return (OK, "")
