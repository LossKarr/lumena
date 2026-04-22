"""
remotion_engine.py — Moteur de rendu vidéo Remotion pour Lumena.

Orchestre:
  1. Scaffolding du projet Remotion (package.json, tsconfig, structure)
  2. Écriture des fichiers de composition (TSX)
  3. Génération du script de rendu (render.mjs)
  4. Exécution dans Docker sandbox (node:20-slim)
  5. Récupération du fichier vidéo (.mp4/.webm/.gif)

Dépendances externes: Docker + image node:20-slim
Dépendances internes: src.utils.docker_sandbox.is_docker_available
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# Import au top-level pour permettre le mock dans les tests
from ..utils.docker_sandbox import is_docker_available

# ── Templates vidéo pré-définis ─────────────────────────────────────

VIDEO_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "presentation": {
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "duration_sec": 30,
        "scenes": ["intro", "features", "demo", "cta"],
        "description_fr": "Présentation produit/service (paysage 16:9)",
    },
    "social_short": {
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "duration_sec": 15,
        "scenes": ["hook", "content", "cta"],
        "description_fr": "Reel/TikTok/Short (portrait 9:16)",
    },
    "explainer": {
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "duration_sec": 60,
        "scenes": ["problem", "solution", "how_it_works", "cta"],
        "description_fr": "Vidéo explicative longue (paysage 16:9)",
    },
    "square_social": {
        "fps": 30,
        "width": 1080,
        "height": 1080,
        "duration_sec": 15,
        "scenes": ["hook", "content", "cta"],
        "description_fr": "Post carré Instagram/LinkedIn (1:1)",
    },
    "custom": {
        "fps": 30,
        "width": 1920,
        "height": 1080,
        "duration_sec": 30,
        "scenes": [],
        "description_fr": "Le LLM décide tout librement",
    },
}

# ── Keywords pour sélection automatique de template ─────────────────

_TEMPLATE_KEYWORDS: Dict[str, List[str]] = {
    "social_short": ["reel", "tiktok", "short", "story", "stories", "vertical", "9:16", "portrait"],
    "square_social": ["carré", "square", "instagram", "linkedin", "1:1"],
    "explainer": ["expliqu", "explain", "tutoriel", "tutorial", "comment", "how to", "guide", "longue"],
    "presentation": ["présent", "present", "produit", "product", "service", "entreprise", "company", "startup", "landing"],
}


# ── P1.1 — Sélection template ──────────────────────────────────────

def select_template(description: str) -> Tuple[str, Dict[str, Any]]:
    """Sélectionne le template vidéo optimal par keyword matching.

    Pattern identique à website_builder.select_palette().

    Returns:
        (template_name, template_dict)
    """
    desc_lower = description.lower()
    best_name = "presentation"  # défaut
    best_score = 0

    for tpl_name, keywords in _TEMPLATE_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in desc_lower)
        if score > best_score:
            best_score = score
            best_name = tpl_name

    return best_name, VIDEO_TEMPLATES[best_name]


# ── P1.2 — Scaffold projet ─────────────────────────────────────────

def scaffold_remotion_project(
    output_dir: Path,
    template: Dict[str, Any],
    composition_id: str = "Main",
) -> Dict[str, str]:
    """Crée le squelette du projet Remotion (fichiers fixes, pas LLM).

    Fichiers générés:
      - package.json (remotion + @remotion/cli + @remotion/renderer + @remotion/bundler)
      - tsconfig.json
      - src/index.ts (registerRoot)
      - src/Root.tsx (Composition wrapper)
      - render.mjs (script de rendu headless)

    Returns:
        Dict des fichiers générés {path_relatif: contenu}
    """
    fps = template["fps"]
    width = template["width"]
    height = template["height"]
    duration_sec = template["duration_sec"]
    total_frames = fps * duration_sec

    # package.json
    package_json = json.dumps({
        "name": "lumena-video",
        "private": True,
        "scripts": {
            "dev": "remotion studio",
            "render": "node render.mjs",
        },
        "dependencies": {
            "remotion": "^4.0.0",
            "@remotion/cli": "^4.0.0",
            "@remotion/renderer": "^4.0.0",
            "@remotion/bundler": "^4.0.0",
            "react": "^18.3.0",
            "react-dom": "^18.3.0",
        },
        "devDependencies": {
            "typescript": "^5.5.0",
            "@types/react": "^18.3.0",
        },
    }, indent=2, ensure_ascii=False)

    # tsconfig.json
    tsconfig_json = json.dumps({
        "compilerOptions": {
            "target": "ESNext",
            "module": "preserve",
            "moduleResolution": "bundler",
            "jsx": "react-jsx",
            "strict": True,
            "esModuleInterop": True,
            "skipLibCheck": True,
            "forceConsistentCasingInFileNames": True,
        },
        "include": ["src/**/*.ts", "src/**/*.tsx"],
    }, indent=2)

    # Root.tsx
    root_tsx = (
        f'import {{ Composition }} from \'remotion\';\n'
        f'import Video from \'./Video\';\n'
        f'\n'
        f'export const RemotionRoot: React.FC = () => {{\n'
        f'  return (\n'
        f'    <Composition\n'
        f'      id="{composition_id}"\n'
        f'      component={{Video}}\n'
        f'      durationInFrames={{{total_frames}}}\n'
        f'      fps={{{fps}}}\n'
        f'      width={{{width}}}\n'
        f'      height={{{height}}}\n'
        f'    />\n'
        f'  );\n'
        f'}};\n'
    )

    # index.ts
    index_ts = (
        "import { registerRoot } from 'remotion';\n"
        "import { RemotionRoot } from './Root';\n"
        "\n"
        "registerRoot(RemotionRoot);\n"
    )

    # render.mjs — licence + GPU options
    license_key = os.getenv("REMOTION_LICENSE_KEY", "").strip()
    license_line = f"  licenseKey: '{license_key}'," if license_key else ""

    gpu_enabled = os.getenv("LUMENA_VIDEO_GPU", "").lower() in ("true", "1", "yes")
    if gpu_enabled:
        gpu_options = "  chromiumOptions: { gl: 'egl' },\n  concurrency: null,"
    else:
        gpu_options = "  concurrency: 1,"

    render_mjs_lines = [
        "// render.mjs — auto-generated by Lumena",
        "import { bundle } from '@remotion/bundler';",
        "import { renderMedia, selectComposition } from '@remotion/renderer';",
        "import path from 'path';",
        "",
        "const serveUrl = await bundle({",
        "  entryPoint: path.join(process.cwd(), './src/index.ts'),",
        "});",
        "",
        "const composition = await selectComposition({",
        "  serveUrl,",
        f"  id: '{composition_id}',",
        "  inputProps: {},",
        "});",
        "",
        "await renderMedia({",
        "  composition,",
        "  serveUrl,",
        "  codec: 'h264',",
        "  outputLocation: 'output.mp4',",
    ]
    if license_line:
        render_mjs_lines.append(license_line)
    render_mjs_lines.append(gpu_options)
    render_mjs_lines.extend([
        "});",
        "",
        "console.log('LUMENA_RENDER_COMPLETE:output.mp4');",
    ])
    render_mjs = "\n".join(render_mjs_lines) + "\n"

    return {
        "package.json": package_json,
        "tsconfig.json": tsconfig_json,
        "src/Root.tsx": root_tsx,
        "src/index.ts": index_ts,
        "render.mjs": render_mjs,
    }


# ── P1.3 — Écriture fichiers scènes ────────────────────────────────

def write_scene_files(
    output_dir: Path,
    scenes_code: Dict[str, str],
) -> None:
    """Écrit les fichiers de scènes TSX générés par le LLM.

    Args:
        output_dir: Racine du projet Remotion
        scenes_code: {"src/scenes/Intro.tsx": "code...", "src/Video.tsx": "code..."}
    """
    for rel_path, content in scenes_code.items():
        fp = output_dir / rel_path
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        logger.debug("[remotion] wrote {}", rel_path)


# ── P1.3b — Gestion des assets utilisateur ─────────────────────────

# Extensions images/vidéo/audio supportées par Remotion
_ASSET_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif"}
_ASSET_VIDEO_EXTS = {".mp4", ".webm", ".mov"}
_ASSET_AUDIO_EXTS = {".mp3", ".wav", ".ogg", ".aac", ".m4a"}
_ASSET_ALL_EXTS = _ASSET_IMAGE_EXTS | _ASSET_VIDEO_EXTS | _ASSET_AUDIO_EXTS


def resolve_asset_paths(assets_raw: List[str]) -> List[Path]:
    """Résout une liste de chemins/noms d'assets en Paths absolus existants.

    Cherche dans l'ordre:
      1. Chemin absolu direct
      2. Relatif au workspace
      3. Dans data/received_images/
      4. Dans data/received_documents/

    Retourne uniquement les fichiers trouvés et supportés par Remotion.
    """
    from ..utils.paths import RECEIVED_IMAGES_DIR, RECEIVED_DOCS_DIR, WORKSPACE_DIR, DATA_DIR

    resolved: List[Path] = []
    for raw in assets_raw:
        raw = raw.strip()
        if not raw:
            continue

        candidates = [
            Path(raw),
            WORKSPACE_DIR / raw,
            DATA_DIR / raw,
            RECEIVED_IMAGES_DIR / Path(raw).name,
            RECEIVED_DOCS_DIR / Path(raw).name,
        ]

        found: Optional[Path] = None
        for c in candidates:
            try:
                if c.exists() and c.is_file():
                    found = c.resolve()
                    break
            except (OSError, ValueError):
                continue

        if found is None:
            logger.warning("[video] Asset introuvable: {} — ignoré", raw)
            continue

        if found.suffix.lower() not in _ASSET_ALL_EXTS:
            logger.warning("[video] Asset ignoré (extension non supportée): {}", found.name)
            continue

        resolved.append(found)
        logger.info("[video] Asset résolu: {}", found.name)

    return resolved


def copy_assets_to_project(project_dir: Path, asset_paths: List[Path]) -> Dict[str, str]:
    """Copie les assets vers public/ du projet Remotion.

    Returns:
        {"nom_fichier.ext": "type"} — type = "image" | "video" | "audio"
        Pour injection dans les prompts LLM.
    """
    if not asset_paths:
        return {}

    public_dir = project_dir / "public"
    public_dir.mkdir(parents=True, exist_ok=True)

    copied: Dict[str, str] = {}
    for src in asset_paths:
        dest = public_dir / src.name
        # Évite les collisions de noms en préfixant si nécessaire
        if dest.exists() and dest.resolve() != src.resolve():
            dest = public_dir / f"asset_{src.name}"

        shutil.copy2(src, dest)

        ext = src.suffix.lower()
        if ext in _ASSET_IMAGE_EXTS:
            asset_type = "image"
        elif ext in _ASSET_VIDEO_EXTS:
            asset_type = "video"
        else:
            asset_type = "audio"

        copied[dest.name] = asset_type
        logger.info("[video] ✅ Asset copié → public/{} ({})", dest.name, asset_type)

    return copied


def build_assets_prompt_section(assets_map: Dict[str, str]) -> str:
    """Génère la section ASSETS pour injection dans les prompts LLM.

    Args:
        assets_map: {"logo.png": "image", "bg.mp4": "video", ...}

    Returns:
        Chaîne multiline prête à être injectée dans les prompts,
        ou "" si pas d'assets.
    """
    if not assets_map:
        return ""

    lines = [
        "",
        "",
        "**ASSETS FOURNIS** (fichiers disponibles dans public/) — INTÈGRE-LES dans la vidéo:",
    ]
    images = [(n, t) for n, t in assets_map.items() if t == "image"]
    videos = [(n, t) for n, t in assets_map.items() if t == "video"]
    audios = [(n, t) for n, t in assets_map.items() if t == "audio"]

    if images:
        lines.append("  Images (utilise `<Img src={staticFile('NOM')} />` ou en background CSS):")
        for name, _ in images:
            lines.append(f"    - {name}")
    if videos:
        lines.append("  Vidéos (utilise `<Video src={staticFile('NOM')} />`):")
        for name, _ in videos:
            lines.append(f"    - {name}")
    if audios:
        lines.append("  Audio (utilise `<Audio src={staticFile('NOM')} />`):")
        for name, _ in audios:
            lines.append(f"    - {name}")

    lines.append("  NOTE: staticFile() est importé depuis 'remotion'")
    return "\n".join(lines)


def auto_detect_recent_assets(max_age_hours: int = 24) -> List[Path]:
    """Détecte automatiquement les assets récemment uploadés par l'utilisateur.

    Cherche dans received_images/ et received_documents/ les fichiers
    uploadés dans les dernières `max_age_hours` heures.

    Returns:
        Liste triée par date (plus récent en premier), limitée à 10 fichiers.
    """
    import time

    from ..utils.paths import RECEIVED_IMAGES_DIR, RECEIVED_DOCS_DIR

    cutoff = time.time() - max_age_hours * 3600
    found: List[Path] = []

    for search_dir in (RECEIVED_IMAGES_DIR, RECEIVED_DOCS_DIR):
        if not search_dir.exists():
            continue
        for f in search_dir.iterdir():
            if (
                f.is_file()
                and f.suffix.lower() in _ASSET_ALL_EXTS
                and f.stat().st_mtime >= cutoff
            ):
                found.append(f)

    found.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return found[:10]


# ── P1.4 — Rendu vidéo (local en priorité, Docker en fallback) ─────

async def _is_node_available() -> bool:
    """Vérifie si Node.js >= 18 est disponible localement."""
    import asyncio

    try:
        proc = await asyncio.create_subprocess_exec(
            "node", "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        return proc.returncode == 0 and b"v" in stdout_bytes
    except Exception:
        return False


async def _render_video_local(project_dir: Path, timeout_sec: int) -> Tuple[Path, str]:
    """Rendu Remotion via Node.js local (pas de Docker).

    Pipeline:
      1. npm install --production (réseau hôte)
      2. node render.mjs
      3. Parse stdout pour 'LUMENA_RENDER_COMPLETE:xxx'
    """
    # Phase 1 : npm install
    logger.info("[video] npm install en cours (peut prendre 30-60s)...")
    stdout, stderr, code = await _run_local_node(
        "npm install --production --no-audit --no-fund",
        workdir=str(project_dir),
        timeout_sec=120,
    )
    if code != 0:
        raise RuntimeError(f"npm install local échoué (exit {code}): {stderr or stdout}")
    logger.info("[video] ✅ npm install terminé")

    # Phase 2 : node render.mjs
    logger.info("[video] Rendu en cours (node render.mjs) — selon nb de frames: 1-3 min...")
    stdout, stderr, code = await _run_local_node(
        "node render.mjs",
        workdir=str(project_dir),
        timeout_sec=timeout_sec,
    )
    if code != 0:
        raise RuntimeError(f"Rendu local échoué (exit {code}): {stderr or stdout}")

    # Phase 3 : parser la sortie
    marker = "LUMENA_RENDER_COMPLETE:"
    for line in stdout.splitlines():
        if marker in line:
            output_file = line.split(marker, 1)[1].strip()
            video_path = project_dir / output_file
            if video_path.exists():
                return video_path, stdout

    raise RuntimeError(f"Rendu local terminé mais fichier vidéo introuvable.\nstdout: {stdout[:500]}")


async def _render_video_docker(project_dir: Path, timeout_sec: int) -> Tuple[Path, str]:
    """Rendu Remotion dans Docker sandbox (fallback).

    Pipeline Docker:
      1. npm install --production (réseau bridge)
      2. node render.mjs (réseau none = sécurisé)
      3. Parse stdout pour 'LUMENA_RENDER_COMPLETE:xxx'
    """
    # Phase 1 : npm install (avec réseau)
    logger.info("[video] npm install Docker en cours (peut prendre 30-60s)...")
    install_cmd = "npm install --production --no-audit --no-fund 2>&1"
    stdout, stderr, code = await _run_in_node_sandbox(
        command=install_cmd,
        workdir=str(project_dir),
        timeout_sec=120,
        network=True,
    )
    if code != 0:
        raise RuntimeError(f"npm install Docker échoué (exit {code}): {stderr or stdout}")
    logger.info("[video] ✅ npm install Docker terminé")

    # Phase 2 : rendu headless (sans réseau)
    logger.info("[video] Rendu Docker en cours (node render.mjs) — selon nb de frames: 1-3 min...")
    render_cmd = "node render.mjs 2>&1"
    stdout, stderr, code = await _run_in_node_sandbox(
        command=render_cmd,
        workdir=str(project_dir),
        timeout_sec=timeout_sec,
        network=False,
    )
    if code != 0:
        raise RuntimeError(f"Rendu Docker échoué (exit {code}): {stderr or stdout}")

    # Phase 3 : parser la sortie
    marker = "LUMENA_RENDER_COMPLETE:"
    for line in stdout.splitlines():
        if marker in line:
            output_file = line.split(marker, 1)[1].strip()
            video_path = project_dir / output_file
            if video_path.exists():
                return video_path, stdout

    raise RuntimeError(f"Rendu Docker terminé mais fichier vidéo introuvable.\nstdout: {stdout[:500]}")


async def render_video_in_docker(
    project_dir: Path,
    timeout_sec: int = 300,
) -> Tuple[Path, str]:
    """Exécute le rendu vidéo Remotion.

    Stratégie (par ordre de priorité):
      1. Node.js local (si disponible et LUMENA_VIDEO_FORCE_DOCKER non activé)
         → Plus rapide, pas de problèmes DNS Docker, accès réseau hôte
      2. Docker sandbox (fallback ou si LUMENA_VIDEO_FORCE_DOCKER=true)

    Returns:
        (path_video, log_output)

    Raises:
        RuntimeError: si le rendu échoue ou timeout
    """
    render_timeout = int(os.getenv("LUMENA_VIDEO_RENDER_TIMEOUT", str(timeout_sec)))
    force_docker = os.getenv("LUMENA_VIDEO_FORCE_DOCKER", "").lower() in ("true", "1", "yes")

    # Priorité 1 : Node.js local (évite les problèmes réseau Docker)
    if not force_docker and await _is_node_available():
        try:
            return await _render_video_local(project_dir, render_timeout)
        except RuntimeError as local_err:
            logger.warning("[Video] Rendu local échoué ({}), tentative Docker...", local_err)

    # Priorité 2 : Docker sandbox
    if not await is_docker_available():
        raise RuntimeError(
            "Docker non disponible et Node.js local introuvable. "
            "Installer Docker Desktop ou Node.js 20+ localement."
        )

    return await _render_video_docker(project_dir, render_timeout)


# ── P1.5 — Docker Node.js sandbox ──────────────────────────────────

async def _run_in_node_sandbox(
    command: str,
    workdir: str,
    timeout_sec: int = 120,
    network: bool = False,
) -> Tuple[str, str, int]:
    """Exécute une commande dans un container Docker node:20-slim.

    Logique de volume mount et limites copiée de docker_sandbox._build_docker_args
    mais avec l'image LUMENA_VIDEO_DOCKER_IMAGE au lieu de _DOCKER_IMAGE.
    """
    import asyncio

    image = os.getenv("LUMENA_VIDEO_DOCKER_IMAGE", "node:20-slim")
    memory = os.getenv("LUMENA_SANDBOX_MEMORY", "512m")
    cpus = os.getenv("LUMENA_SANDBOX_CPUS", "1")
    pids_limit = os.getenv("LUMENA_SANDBOX_PIDS_LIMIT", "256")
    gpu_enabled = os.getenv("LUMENA_VIDEO_GPU", "").lower() in ("true", "1", "yes")

    args = ["docker", "run", "--rm", "--memory", memory, "--cpus", cpus, "--pids-limit", pids_limit]

    if gpu_enabled:
        args += ["--gpus", "all"]

    if network:
        args += ["--network", "bridge"]
    else:
        args += ["--network", "none"]

    workdir_path = Path(workdir).resolve()
    if workdir_path.exists():
        mount_src = str(workdir_path).replace("\\", "/")
        args += ["-v", f"{mount_src}:/work:rw", "-w", "/work"]

    args += [image, "bash", "-c", command]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"Timeout après {timeout_sec}s", -1

    return (
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )


# ── P1.6 — Fallback Node.js local ──────────────────────────────────

async def _run_local_node(
    command: str,
    workdir: str,
    timeout_sec: int = 120,
) -> Tuple[str, str, int]:
    """Fallback: exécute via Node.js local si Docker indisponible et LUMENA_SANDBOX_MODE=never."""
    import asyncio

    # Résolution absolue du chemin pour éviter WinError 267 sur chemins accentués (Windows)
    _resolved_workdir = str(Path(workdir).resolve())

    proc = await asyncio.create_subprocess_shell(
        command,
        cwd=_resolved_workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_sec
        )
    except asyncio.TimeoutError:
        proc.kill()
        return "", f"Timeout après {timeout_sec}s", -1

    return (
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
