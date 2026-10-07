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
import hashlib
import os
import shlex
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

from loguru import logger

# Import au top-level pour permettre le mock dans les tests
from ..utils.docker_sandbox import is_docker_available


REMOTION_VERSION = "4.0.530"
REACT_VERSION = "18.3.1"
TYPESCRIPT_VERSION = "5.5.4"
DEFAULT_VIDEO_DOCKER_IMAGE = "lumena-remotion-runtime:4.0.530-node20.19.5-v2"
_LEGACY_VIDEO_BASE_IMAGES = {
    "node:20-slim",
    "node:20-bookworm-slim",
    "node:20.19.5-bookworm-slim",
    "lumena-remotion-runtime:4.0.530-node20.19.5-v1",
}
_MAX_ASSET_BYTES = 512 * 1024 * 1024
_DEPENDENCY_STAMP = ".lumena-video-deps.json"
_DEPENDENCY_LOCK = ".lumena-video-install.lock"


class VideoInfrastructureError(RuntimeError):
    """Échec Docker, Node, réseau ou dépendances non réparable par le LLM."""


class VideoRenderError(RuntimeError):
    """Échec de compilation ou rendu susceptible de provenir du projet TSX."""


class VideoCancelledError(RuntimeError):
    """Annulation utilisateur observée pendant une commande vidéo."""

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
            "remotion": REMOTION_VERSION,
            "@remotion/cli": REMOTION_VERSION,
            "@remotion/renderer": REMOTION_VERSION,
            "@remotion/bundler": REMOTION_VERSION,
            "react": REACT_VERSION,
            "react-dom": REACT_VERSION,
        },
        "devDependencies": {
            "typescript": TYPESCRIPT_VERSION,
            "@types/react": "18.3.3",
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

    # render.mjs — la clé de licence reste uniquement dans l'environnement du
    # processus. Elle ne doit jamais être copiée dans un projet partageable.
    gpu_enabled = os.getenv("LUMENA_VIDEO_GPU", "").lower() in ("true", "1", "yes")
    if gpu_enabled:
        gpu_options = "  chromiumOptions: { gl: 'egl' },\n  concurrency: null,"
    else:
        gpu_options = "  concurrency: 1,"

    render_mjs_lines = [
        "// render.mjs — auto-generated by Lumena",
        "import { bundle } from '@remotion/bundler';",
        "import { renderMedia, renderStill, selectComposition } from '@remotion/renderer';",
        "import path from 'path';",
        "import fs from 'fs';",
        "",
        "const licenseKey = process.env.REMOTION_LICENSE_KEY || undefined;",
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
        "  pixelFormat: 'yuv420p',",
        "  outputLocation: 'output.mp4',",
        "  ...(licenseKey ? {licenseKey} : {}),",
    ]
    render_mjs_lines.append(gpu_options)
    render_mjs_lines.extend([
        "});",
        "",
        "fs.mkdirSync('quality', {recursive: true});",
        "const proofFrames = [0, Math.floor(composition.durationInFrames / 2), Math.max(0, composition.durationInFrames - 1)];",
        "for (const [index, frame] of proofFrames.entries()) {",
        "  await renderStill({",
        "    composition,",
        "    serveUrl,",
        "    frame,",
        "    output: `quality/frame-${index + 1}.png`,",
        "    ...(licenseKey ? {licenseKey} : {}),",
        "  });",
        "}",
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

    allowed_roots = [
        WORKSPACE_DIR.resolve(),
        DATA_DIR.resolve(),
        RECEIVED_IMAGES_DIR.resolve(),
        RECEIVED_DOCS_DIR.resolve(),
    ]
    configured_roots = os.getenv("LUMENA_VIDEO_ASSET_ROOTS", "")
    for configured in configured_roots.split(os.pathsep):
        if configured.strip():
            try:
                allowed_roots.append(Path(configured.strip()).expanduser().resolve(strict=True))
            except (OSError, ValueError):
                logger.warning("[video] Racine d'assets configurée invalide: {}", configured)

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

        if not any(_is_relative_to(found, root) for root in allowed_roots):
            logger.warning("[video] Asset hors des racines autorisées: {} — ignoré", found)
            continue

        if found.suffix.lower() not in _ASSET_ALL_EXTS:
            logger.warning("[video] Asset ignoré (extension non supportée): {}", found.name)
            continue

        try:
            if found.stat().st_size > _MAX_ASSET_BYTES:
                logger.warning("[video] Asset trop volumineux: {} — ignoré", found.name)
                continue
        except OSError:
            logger.warning("[video] Asset illisible: {} — ignoré", found)
            continue

        resolved.append(found)
        logger.info("[video] Asset résolu: {}", found.name)

    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    """Retourne vrai si ``path`` reste sous ``root`` après résolution."""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def resolve_video_project_path(project_dir: Union[str, Path]) -> Path:
    """Résout un projet Remotion et impose la racine workspace de Lumena."""
    from ..utils.paths import WORKSPACE_DIR

    workspace = WORKSPACE_DIR.resolve()
    candidate = Path(project_dir).expanduser().resolve()
    if not _is_relative_to(candidate, workspace):
        raise ValueError("Le projet vidéo doit rester dans le workspace Lumena.")
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError(f"Projet vidéo introuvable: {candidate}")
    return candidate


def resolve_render_output(project_dir: Path, output_file: str) -> Path:
    """Résout le marqueur de sortie sans autoriser d'évasion du projet."""
    output_name = (output_file or "").strip()
    if not output_name:
        raise RuntimeError("Le moteur n'a pas annoncé de fichier de sortie.")
    candidate = (project_dir / output_name).resolve()
    if not _is_relative_to(candidate, project_dir.resolve()):
        raise RuntimeError("Le moteur a annoncé une sortie hors du projet autorisé.")
    if candidate.suffix.lower() not in {".mp4", ".webm", ".gif"}:
        raise RuntimeError("Le moteur a annoncé un format de sortie non autorisé.")
    return candidate


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


# ── P1.4 — Rendu vidéo (Docker par défaut, hôte explicitement autorisé) ─────

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


def _video_install_timeout() -> int:
    """Retourne un timeout d'installation distinct du timeout de rendu."""
    try:
        value = int(os.getenv("LUMENA_VIDEO_INSTALL_TIMEOUT", "900"))
    except ValueError:
        value = 900
    return max(60, min(value, 3600))


def _video_docker_image() -> str:
    """Retourne l'image de rendu, avec migration des anciennes images Node.

    Les anciennes valeurs étaient des images Node brutes : elles ne possèdent
    pas les bibliothèques partagées requises par Chrome Headless Shell. Elles
    sont donc migrées sans modifier le fichier ``.env`` de l'utilisateur.
    Une image personnalisée reste honorée telle quelle.
    """
    configured = os.getenv("LUMENA_VIDEO_DOCKER_IMAGE", "").strip()
    if not configured or configured in _LEGACY_VIDEO_BASE_IMAGES:
        if configured:
            logger.warning(
                "[video] Image Node historique '{}' remplacée par le runtime Remotion certifié.",
                configured,
            )
        return DEFAULT_VIDEO_DOCKER_IMAGE
    return configured


async def _ensure_video_runtime_image() -> None:
    """Construit à la demande le runtime Remotion fourni avec Lumena."""
    import asyncio

    image = _video_docker_image()
    if image != DEFAULT_VIDEO_DOCKER_IMAGE:
        return

    inspect = await asyncio.create_subprocess_exec(
        "docker",
        "image",
        "inspect",
        image,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(inspect.wait(), timeout=20)
    except asyncio.TimeoutError as exc:
        inspect.kill()
        raise VideoInfrastructureError(
            "Docker ne répond pas pendant la vérification du runtime vidéo."
        ) from exc
    if inspect.returncode == 0:
        return

    docker_context = Path(__file__).resolve().parent / "remotion_runtime"
    dockerfile = docker_context / "Dockerfile"
    if not dockerfile.is_file():
        raise VideoInfrastructureError(
            f"Runtime vidéo absent et Dockerfile Remotion introuvable: {dockerfile}"
        )

    logger.info("[video] Construction initiale du runtime Remotion '{}'...", image)
    process = await asyncio.create_subprocess_exec(
        "docker",
        "build",
        "--pull",
        "-t",
        image,
        str(docker_context),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=_video_install_timeout(),
        )
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise VideoInfrastructureError(
            "La construction initiale du runtime vidéo a dépassé le délai autorisé."
        ) from exc
    if process.returncode != 0:
        details = (stderr or stdout).decode("utf-8", errors="replace")
        raise VideoInfrastructureError(
            f"Construction du runtime vidéo échouée (exit {process.returncode}): {details[-3000:]}"
        )
    logger.info("[video] ✅ runtime Remotion construit et certifié")


def _lockfile_digest(project_dir: Path) -> str:
    lockfile = project_dir / "package-lock.json"
    if not lockfile.is_file():
        return ""
    return hashlib.sha256(lockfile.read_bytes()).hexdigest()


def _dependencies_ready(project_dir: Path, runtime_id: str) -> bool:
    """Vérifie que node_modules correspond exactement au lockfile courant."""
    stamp_path = project_dir / _DEPENDENCY_STAMP
    node_modules = project_dir / "node_modules"
    if not node_modules.is_dir() or not stamp_path.is_file():
        return False
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return (
        stamp.get("lock_sha256") == _lockfile_digest(project_dir)
        and stamp.get("runtime") == runtime_id
        and stamp.get("remotion_version") == REMOTION_VERSION
    )


def _write_dependency_stamp(project_dir: Path, runtime_id: str) -> None:
    payload = {
        "lock_sha256": _lockfile_digest(project_dir),
        "runtime": runtime_id,
        "remotion_version": REMOTION_VERSION,
        "installed_at": int(time.time()),
    }
    path = project_dir / _DEPENDENCY_STAMP
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temp.replace(path)


def _clean_partial_dependencies(project_dir: Path) -> None:
    """Supprime uniquement les dépendances générées dans le projet borné."""
    node_modules = (project_dir / "node_modules").resolve()
    try:
        node_modules.relative_to(project_dir.resolve())
    except ValueError as exc:
        raise VideoInfrastructureError("node_modules hors du projet autorisé") from exc
    if node_modules.is_symlink():
        node_modules.unlink(missing_ok=True)
    elif node_modules.exists():
        shutil.rmtree(node_modules)
    (project_dir / _DEPENDENCY_STAMP).unlink(missing_ok=True)


@contextmanager
def _dependency_install_lock(project_dir: Path, timeout_sec: int) -> Iterator[None]:
    """Empêche deux processus npm d'écrire dans le même projet."""
    lock_path = project_dir / _DEPENDENCY_LOCK
    token = uuid.uuid4().hex
    for attempt in range(2):
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(json.dumps({"token": token, "pid": os.getpid(), "created_at": time.time()}))
            break
        except FileExistsError as exc:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0
            if attempt == 0 and age > timeout_sec + 60:
                lock_path.unlink(missing_ok=True)
                continue
            raise VideoInfrastructureError(
                "Une installation Remotion est déjà active pour ce projet."
            ) from exc
    try:
        yield
    finally:
        try:
            current = json.loads(lock_path.read_text(encoding="utf-8"))
            if current.get("token") == token:
                lock_path.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass


async def _render_video_local(project_dir: Path, timeout_sec: int) -> Tuple[Path, str]:
    """Rendu Remotion via Node.js local, réservé au développement explicite.

    Pipeline:
      1. npm install --production (réseau hôte)
      2. node render.mjs
      3. Parse stdout pour 'LUMENA_RENDER_COMPLETE:xxx'
    """
    # Phase 1 : dépendances reproductibles, installées une seule fois par lockfile.
    install_timeout = _video_install_timeout()
    runtime_id = "host-node"
    await _ensure_local_lockfile(project_dir)
    with _dependency_install_lock(project_dir, install_timeout):
        if not _dependencies_ready(project_dir, runtime_id):
            logger.info("[video] npm ci local en cours (timeout {}s)...", install_timeout)
            _clean_partial_dependencies(project_dir)
            stdout, stderr, code = await _run_local_node(
                ["npm", "ci", "--omit=dev", "--no-audit", "--no-fund"],
                workdir=str(project_dir),
                timeout_sec=install_timeout,
            )
            if code == -2:
                _clean_partial_dependencies(project_dir)
                raise VideoCancelledError(stderr or "Installation annulée")
            if code != 0:
                _clean_partial_dependencies(project_dir)
                raise VideoInfrastructureError(
                    f"npm ci local échoué (exit {code}): {stderr or stdout}"
                )
            _write_dependency_stamp(project_dir, runtime_id)
            logger.info("[video] ✅ npm ci local terminé")
        else:
            logger.info("[video] ✅ dépendances locales déjà certifiées par le lockfile")

    # Phase 2 : node render.mjs
    logger.info("[video] Rendu en cours (node render.mjs) — selon nb de frames: 1-3 min...")
    stdout, stderr, code = await _run_local_node(
        ["node", "render.mjs"],
        workdir=str(project_dir),
        timeout_sec=timeout_sec,
    )
    if code == -2:
        raise VideoCancelledError(stderr or "Rendu annulé")
    if code != 0:
        raise VideoRenderError(f"Rendu local échoué (exit {code}): {stderr or stdout}")

    # Phase 3 : parser la sortie
    marker = "LUMENA_RENDER_COMPLETE:"
    for line in stdout.splitlines():
        if marker in line:
            output_file = line.split(marker, 1)[1].strip()
            video_path = resolve_render_output(project_dir, output_file)
            if video_path.exists():
                return video_path, stdout

    raise VideoRenderError(f"Rendu local terminé mais fichier vidéo introuvable.\nstdout: {stdout[:500]}")


async def _render_video_docker(project_dir: Path, timeout_sec: int) -> Tuple[Path, str]:
    """Rendu Remotion dans le sandbox Docker par défaut.

    Pipeline Docker:
      1. npm install --production (réseau bridge)
      2. vérification/téléchargement du navigateur Remotion (réseau bridge)
      3. node render.mjs (réseau none = sécurisé)
      4. Parse stdout pour 'LUMENA_RENDER_COMPLETE:xxx'
    """
    # Phase 1 : lockfile + installation unique sous verrou inter-processus.
    install_timeout = _video_install_timeout()
    image = _video_docker_image()
    if not (project_dir / "package-lock.json").exists():
        logger.info("[video] Création du lockfile Docker (timeout {}s)...", install_timeout)
        lock_stdout, lock_stderr, lock_code = await _run_in_node_sandbox(
            command=["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
            workdir=str(project_dir),
            timeout_sec=install_timeout,
            network=True,
        )
        if lock_code == -2:
            raise VideoCancelledError(lock_stderr or "Création du lockfile annulée")
        if lock_code != 0:
            raise VideoInfrastructureError(
                f"Création du lockfile Docker échouée (exit {lock_code}): "
                f"{lock_stderr or lock_stdout}"
            )
    with _dependency_install_lock(project_dir, install_timeout):
        if not _dependencies_ready(project_dir, image):
            logger.info("[video] npm ci Docker en cours (timeout {}s)...", install_timeout)
            _clean_partial_dependencies(project_dir)
            stdout, stderr, code = await _run_in_node_sandbox(
                command=["npm", "ci", "--omit=dev", "--no-audit", "--no-fund"],
                workdir=str(project_dir),
                timeout_sec=install_timeout,
                network=True,
            )
            if code == -2:
                _clean_partial_dependencies(project_dir)
                raise VideoCancelledError(stderr or "Installation Docker annulée")
            if code != 0:
                _clean_partial_dependencies(project_dir)
                raise VideoInfrastructureError(
                    f"npm ci Docker échoué (exit {code}): {stderr or stdout}"
                )
            _write_dependency_stamp(project_dir, image)
            logger.info("[video] ✅ npm ci Docker terminé")
        else:
            logger.info("[video] ✅ dépendances Docker déjà certifiées par le lockfile")

        # Remotion télécharge Chrome Headless Shell à la première utilisation.
        # Cette étape doit impérativement se produire pendant la phase réseau :
        # le rendu suivant est volontairement exécuté avec ``--network none``.
        # Le cache vit dans node_modules/.remotion, donc dans le volume persistant
        # du projet, et la commande devient une simple vérification aux rendus
        # suivants.
        logger.info("[video] Vérification du navigateur Remotion...")
        browser_stdout, browser_stderr, browser_code = await _run_in_node_sandbox(
            command=[
                "node",
                "node_modules/@remotion/cli/remotion-cli.js",
                "browser",
                "ensure",
                "--log=info",
            ],
            workdir=str(project_dir),
            timeout_sec=install_timeout,
            network=True,
        )
        if browser_code == -2:
            raise VideoCancelledError(
                browser_stderr or "Préparation du navigateur Remotion annulée"
            )
        if browser_code != 0:
            raise VideoInfrastructureError(
                f"Préparation du navigateur Remotion échouée (exit {browser_code}): "
                f"{browser_stderr or browser_stdout}"
            )
        logger.info("[video] ✅ navigateur Remotion disponible hors ligne")

    # Phase 2 : rendu headless (sans réseau)
    logger.info("[video] Rendu Docker en cours (node render.mjs) — selon nb de frames: 1-3 min...")
    stdout, stderr, code = await _run_in_node_sandbox(
        command=["node", "render.mjs"],
        workdir=str(project_dir),
        timeout_sec=timeout_sec,
        network=False,
    )
    if code == -2:
        raise VideoCancelledError(stderr or "Rendu Docker annulé")
    if code != 0:
        raise VideoRenderError(f"Rendu Docker échoué (exit {code}): {stderr or stdout}")

    # Phase 3 : parser la sortie
    marker = "LUMENA_RENDER_COMPLETE:"
    for line in stdout.splitlines():
        if marker in line:
            output_file = line.split(marker, 1)[1].strip()
            video_path = resolve_render_output(project_dir, output_file)
            if video_path.exists():
                return video_path, stdout

    raise VideoRenderError(f"Rendu Docker terminé mais fichier vidéo introuvable.\nstdout: {stdout[:500]}")


async def render_video_in_docker(
    project_dir: Path,
    timeout_sec: int = 300,
) -> Tuple[Path, str]:
    """Exécute le rendu vidéo Remotion.

    Stratégie sûre:
      1. Docker sandbox par défaut.
      2. Node.js local uniquement si LUMENA_VIDEO_ALLOW_HOST_NODE=true.

    Returns:
        (path_video, log_output)

    Raises:
        RuntimeError: si le rendu échoue ou timeout
    """
    project_dir = resolve_video_project_path(project_dir)
    render_timeout = int(os.getenv("LUMENA_VIDEO_RENDER_TIMEOUT", str(timeout_sec)))
    allow_host = os.getenv("LUMENA_VIDEO_ALLOW_HOST_NODE", "").lower() in ("true", "1", "yes")

    if await is_docker_available():
        await _ensure_video_runtime_image()
        return await _render_video_docker(project_dir, render_timeout)

    if allow_host and await _is_node_available():
        logger.warning("[video] Rendu Node hôte explicitement autorisé; isolation réduite.")
        return await _render_video_local(project_dir, render_timeout)

    raise VideoInfrastructureError(
        "Docker est requis pour isoler le code Remotion généré. "
        "Le rendu Node hôte peut être autorisé explicitement avec "
        "LUMENA_VIDEO_ALLOW_HOST_NODE=true pour le développement."
    )


async def probe_video_in_docker(project_dir: Path, video_path: Path) -> Dict[str, Any]:
    """Inspecte un média avec FFprobe dans le runtime vidéo fourni.

    Ce fallback rend le contrôle qualité autonome sur Windows : aucune
    installation FFmpeg sur l'hôte n'est nécessaire.
    """
    project = resolve_video_project_path(project_dir)
    candidate = video_path.expanduser().resolve()
    if not _is_relative_to(candidate, project):
        raise VideoInfrastructureError("Le média à inspecter est hors du projet vidéo.")
    if not candidate.is_file():
        raise VideoInfrastructureError(f"Média à inspecter introuvable: {candidate.name}")

    await _ensure_video_runtime_image()
    relative = candidate.relative_to(project).as_posix()
    stdout, stderr, code = await _run_in_node_sandbox(
        command=[
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            f"/work/{relative}",
        ],
        workdir=str(project),
        timeout_sec=30,
        network=False,
    )
    if code != 0:
        raise VideoInfrastructureError(
            f"FFprobe Docker échoué (exit {code}): {stderr or stdout}"
        )
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise VideoInfrastructureError("FFprobe Docker a retourné un JSON invalide.") from exc
    if not isinstance(payload, dict):
        raise VideoInfrastructureError("FFprobe Docker a retourné un résultat invalide.")
    return payload


# ── P1.5 — Docker Node.js sandbox ──────────────────────────────────

async def _run_in_node_sandbox(
    command: Union[str, Sequence[str]],
    workdir: str,
    timeout_sec: int = 120,
    network: bool = False,
    container_name: str = "",
) -> Tuple[str, str, int]:
    """Exécute une commande dans un conteneur Docker Node épinglé.

    Logique de volume mount et limites copiée de docker_sandbox._build_docker_args
    mais avec l'image LUMENA_VIDEO_DOCKER_IMAGE au lieu de _DOCKER_IMAGE.
    """
    import asyncio

    image = _video_docker_image()
    memory = os.getenv("LUMENA_VIDEO_DOCKER_MEMORY", "2g")
    cpus = os.getenv("LUMENA_VIDEO_DOCKER_CPUS", "2")
    pids_limit = os.getenv("LUMENA_VIDEO_DOCKER_PIDS_LIMIT", "512")
    gpu_enabled = os.getenv("LUMENA_VIDEO_GPU", "").lower() in ("true", "1", "yes")
    workdir_path = Path(workdir).resolve()
    if not container_name:
        project_digest = hashlib.sha256(str(workdir_path).encode("utf-8")).hexdigest()[:10]
        container_name = f"lumena-video-{project_digest}-{uuid.uuid4().hex[:8]}"

    args = [
        "docker", "run", "--rm",
        "--name", container_name,
        "--memory", memory,
        "--cpus", cpus,
        "--pids-limit", pids_limit,
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=512m",
        "--env", "NPM_CONFIG_CACHE=/tmp/npm-cache",
    ]

    # Docker reçoit la clé par héritage d'environnement sans que sa valeur
    # apparaisse dans la ligne de commande, les sources ou les journaux.
    if os.getenv("REMOTION_LICENSE_KEY"):
        args += ["--env", "REMOTION_LICENSE_KEY"]

    if gpu_enabled:
        args += ["--gpus", "all"]

    if network:
        args += ["--network", "bridge"]
    else:
        args += ["--network", "none"]

    if workdir_path.exists():
        mount_src = str(workdir_path).replace("\\", "/")
        args += ["-v", f"{mount_src}:/work:rw", "-w", "/work"]

    command_args = shlex.split(command, posix=os.name != "nt") if isinstance(command, str) else list(command)
    if not command_args:
        raise ValueError("Commande Docker vide")
    args += [image, *command_args]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_bytes, stderr_bytes, process_code = await _communicate_with_cancel(
        proc,
        timeout_sec=timeout_sec,
        cancel_file=workdir_path / ".lumena-video-cancel",
    )
    if process_code < 0:
        await _force_remove_container(container_name)
        return "", stderr_bytes.decode("utf-8", errors="replace"), process_code

    return (
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )


async def _force_remove_container(container_name: str) -> None:
    """Détruit le conteneur possédé par Lumena après timeout ou annulation."""
    import asyncio

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await asyncio.wait_for(proc.communicate(), timeout=20)
    except Exception as exc:
        logger.warning(
            "[video] Nettoyage du conteneur {} non confirmé: {}",
            container_name,
            exc,
        )


# ── P1.6 — Fallback Node.js local ──────────────────────────────────

async def _run_local_node(
    command: Union[str, Sequence[str]],
    workdir: str,
    timeout_sec: int = 120,
) -> Tuple[str, str, int]:
    """Fallback: exécute via Node.js local si Docker indisponible et LUMENA_SANDBOX_MODE=never."""
    import asyncio

    # Résolution absolue du chemin pour éviter WinError 267 sur chemins accentués (Windows)
    _resolved_workdir = str(Path(workdir).resolve())

    command_args = shlex.split(command, posix=os.name != "nt") if isinstance(command, str) else list(command)
    if not command_args:
        raise ValueError("Commande Node vide")
    # Sous Windows, npm et npx sont des shims ``.cmd``. CreateProcess ne
    # consulte pas toujours PATHEXT comme un shell ; on résout donc le binaire
    # explicitement tout en gardant une exécution argv sans shell.
    resolved_executable = shutil.which(command_args[0])
    if resolved_executable:
        command_args[0] = resolved_executable
    proc = await asyncio.create_subprocess_exec(
        *command_args,
        cwd=_resolved_workdir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stdout_bytes, stderr_bytes, process_code = await _communicate_with_cancel(
        proc,
        timeout_sec=timeout_sec,
        cancel_file=Path(_resolved_workdir) / ".lumena-video-cancel",
    )
    if process_code < 0:
        return "", stderr_bytes.decode("utf-8", errors="replace"), process_code

    return (
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_bytes.decode("utf-8", errors="replace"),
        proc.returncode or 0,
    )


async def _communicate_with_cancel(
    proc: Any,
    *,
    timeout_sec: int,
    cancel_file: Path,
) -> Tuple[bytes, bytes, int]:
    """Attend un processus tout en honorant une annulation persistante."""
    import asyncio
    import time

    task = asyncio.create_task(proc.communicate())
    deadline = time.monotonic() + timeout_sec
    while True:
        done, _ = await asyncio.wait({task}, timeout=0.25)
        if task in done:
            try:
                stdout, stderr = task.result()
                return stdout, stderr, int(proc.returncode or 0)
            except asyncio.TimeoutError:
                proc.kill()
                return b"", f"Timeout après {timeout_sec}s".encode(), -1
        if cancel_file.exists():
            proc.kill()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                pass
            return b"", "Annulation demandée par l'utilisateur".encode("utf-8"), -2
        if time.monotonic() >= deadline:
            proc.kill()
            try:
                await asyncio.wait_for(task, timeout=5)
            except (asyncio.TimeoutError, Exception):
                pass
            return b"", f"Timeout après {timeout_sec}s".encode(), -1


async def _ensure_local_lockfile(project_dir: Path) -> None:
    """Crée une fois le lockfile, puis tous les installs passent par ``npm ci``."""
    if (project_dir / "package-lock.json").exists():
        return
    stdout, stderr, code = await _run_local_node(
        ["npm", "install", "--package-lock-only", "--ignore-scripts", "--no-audit", "--no-fund"],
        workdir=str(project_dir),
        timeout_sec=_video_install_timeout(),
    )
    if code == -2:
        raise VideoCancelledError(stderr or "Création du lockfile annulée")
    if code != 0:
        raise VideoInfrastructureError(
            f"Création du lockfile locale échouée (exit {code}): {stderr or stdout}"
        )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
