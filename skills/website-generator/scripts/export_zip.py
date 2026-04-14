#!/usr/bin/env python3
"""
Website ZIP Exporter — Exporte un projet web en ZIP avec progression temps réel.

Usage:
    python scripts/export_zip.py <project-name-or-path> [--output <zip-path>]

Examples:
    python scripts/export_zip.py mon-restaurant
    python scripts/export_zip.py ./workspace/saas-fitness --output ~/Desktop/site.zip
    python scripts/export_zip.py portfolio-design

Ce script:
1. Scanne tous les fichiers du projet
2. Crée un ZIP avec compression
3. Affiche la progression en temps réel (barre + fichier courant)
4. Affiche les statistiques finales (taille, fichiers, ratio compression)
"""

import argparse
import os
import sys
import time
import zipfile
from pathlib import Path


# Dossiers/fichiers à ignorer
IGNORE_PATTERNS = {
    "__pycache__", ".git", ".DS_Store", "node_modules", ".env",
    "Thumbs.db", ".vscode", ".idea", "*.pyc", ".gitignore"
}


def should_ignore(path: Path) -> bool:
    """Vérifie si un fichier/dossier doit être ignoré."""
    for part in path.parts:
        if part in IGNORE_PATTERNS:
            return True
        if part.startswith(".") and part not in (".", "..", ".htaccess"):
            return True
    return False


def format_size(size_bytes: int) -> str:
    """Formate une taille en format lisible."""
    for unit in ["o", "Ko", "Mo", "Go"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} To"


def print_progress(filename: str, current: int, total: int, bar_width: int = 30):
    """Affiche une barre de progression avec le nom du fichier."""
    percent = (current / total) * 100
    filled = int(bar_width * current / total)
    bar = "█" * filled + "░" * (bar_width - filled)
    
    # Tronquer le nom de fichier si trop long
    max_name_len = 40
    display_name = filename if len(filename) <= max_name_len else "..." + filename[-(max_name_len - 3):]
    
    # \r pour réécrire la ligne
    sys.stdout.write(f"\r  [{bar}] {percent:5.1f}% ({current}/{total}) — {display_name:<{max_name_len}}")
    sys.stdout.flush()


def resolve_project_path(name_or_path: str) -> Path:
    """Résout le nom de projet en chemin absolu."""
    path = Path(name_or_path)
    
    if path.exists():
        return path.resolve()
    
    lumena_root = Path(__file__).parent.parent.parent
    workspace = lumena_root / "workspace"
    
    candidate = workspace / name_or_path
    if candidate.exists():
        return candidate
    
    if workspace.exists():
        for d in workspace.iterdir():
            if d.is_dir() and name_or_path.lower() in d.name.lower():
                return d
    
    return path


def export_zip(project_dir: Path, output_path: Path) -> dict:
    """Exporte le projet en ZIP avec progression."""
    
    # Collecter les fichiers
    all_files = []
    for root, dirs, files in os.walk(project_dir):
        # Filtrer les dossiers ignorés
        dirs[:] = [d for d in dirs if not should_ignore(Path(d))]
        
        for f in files:
            full_path = Path(root) / f
            if not should_ignore(full_path):
                rel_path = full_path.relative_to(project_dir)
                all_files.append((full_path, str(rel_path)))
    
    if not all_files:
        return {"success": False, "error": "Aucun fichier trouvé"}
    
    total = len(all_files)
    total_size_raw = sum(f[0].stat().st_size for f in all_files)
    
    print(f"\n📦 Export ZIP — {total} fichiers ({format_size(total_size_raw)})\n")
    
    start_time = time.time()
    
    # Créer le ZIP
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with zipfile.ZipFile(str(output_path), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for i, (full_path, rel_path) in enumerate(all_files):
            zf.write(full_path, rel_path)
            print_progress(rel_path, i + 1, total)
    
    elapsed = time.time() - start_time
    zip_size = output_path.stat().st_size
    
    # Ratio de compression
    if total_size_raw > 0:
        ratio = (1 - zip_size / total_size_raw) * 100
    else:
        ratio = 0
    
    print(f"\n\n✅ ZIP exporté avec succès !\n")
    print(f"  📦 Fichier      : {output_path}")
    print(f"  📊 Taille brute : {format_size(total_size_raw)}")
    print(f"  📦 Taille ZIP   : {format_size(zip_size)}")
    print(f"  🔄 Compression  : {ratio:.1f}%")
    print(f"  📁 Fichiers     : {total}")
    print(f"  ⏱️  Durée        : {elapsed:.2f}s")
    print()
    
    return {
        "success": True,
        "zip_path": str(output_path),
        "size_raw": total_size_raw,
        "size_zip": zip_size,
        "compression_ratio": ratio,
        "files_count": total,
        "elapsed": elapsed,
    }


def main():
    parser = argparse.ArgumentParser(description="Exporte un projet web en ZIP avec progression")
    parser.add_argument("project", help="Nom du projet ou chemin du dossier")
    parser.add_argument("--output", "-o", default="", help="Chemin de sortie du ZIP")
    
    args = parser.parse_args()
    
    project_path = resolve_project_path(args.project)
    
    if not project_path.exists():
        print(f"❌ Projet introuvable : {project_path}")
        sys.exit(1)
    
    if args.output:
        output_path = Path(args.output)
        if not output_path.suffix:
            output_path = output_path / f"{project_path.name}.zip"
    else:
        output_path = project_path.parent / f"{project_path.name}.zip"
    
    result = export_zip(project_path, output_path)
    
    if not result["success"]:
        print(f"❌ {result.get('error', 'Erreur inconnue')}")
        sys.exit(1)


if __name__ == "__main__":
    main()
