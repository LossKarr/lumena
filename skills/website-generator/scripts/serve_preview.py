#!/usr/bin/env python3
"""
Website Preview Server — Lance un serveur HTTP local avec auto-reload.

Usage:
    python scripts/serve_preview.py <project-name-or-path> [--port 8080] [--no-open]

Examples:
    python scripts/serve_preview.py mon-restaurant
    python scripts/serve_preview.py ./workspace/mon-site --port 3000
    python scripts/serve_preview.py saas-fitness --no-open

Ce script:
1. Trouve le dossier contenant index.html
2. Lance un serveur HTTP local
3. Ouvre le navigateur automatiquement
4. Affiche les requêtes en temps réel
"""

import argparse
import os
import signal
import socket
import sys
import webbrowser
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path


class QuietHandler(SimpleHTTPRequestHandler):
    """Handler HTTP avec logs colorés et silencieux sur les fichiers statiques."""
    
    def log_message(self, format, *args):
        status = args[1] if len(args) > 1 else ""
        path = args[0] if args else ""
        
        # Coloriser selon le status
        if "200" in str(status):
            color = "\033[92m"  # Vert
        elif "304" in str(status):
            color = "\033[93m"  # Jaune
        elif "404" in str(status):
            color = "\033[91m"  # Rouge
        else:
            color = "\033[0m"
        
        # Ignorer les favicon et assets communs
        if ".ico" in str(path) or ".map" in str(path):
            return
        
        reset = "\033[0m"
        print(f"  {color}{format % args}{reset}")
    
    def end_headers(self):
        # CORS headers pour dev local
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()


def find_serve_directory(project_path: Path) -> Path:
    """Trouve le meilleur dossier à servir (celui avec index.html)."""
    # Chemin direct
    if (project_path / "index.html").exists():
        return project_path
    
    # Sous-dossier public/
    if (project_path / "public" / "index.html").exists():
        return project_path / "public"
    
    # Recherche récursive
    for html in project_path.rglob("index.html"):
        return html.parent
    
    return project_path


def find_free_port(start_port: int) -> int:
    """Trouve un port libre à partir du port de départ."""
    for port in range(start_port, start_port + 100):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("localhost", port))
                return port
        except OSError:
            continue
    return start_port


def resolve_project_path(name_or_path: str) -> Path:
    """Résout le nom de projet en chemin absolu."""
    path = Path(name_or_path)
    
    # Chemin absolu ou relatif existant
    if path.exists():
        return path.resolve()
    
    # Chercher dans workspace/
    lumena_root = Path(__file__).parent.parent.parent
    workspace = lumena_root / "workspace"
    
    candidate = workspace / name_or_path
    if candidate.exists():
        return candidate
    
    # Chercher par pattern
    if workspace.exists():
        for d in workspace.iterdir():
            if d.is_dir() and name_or_path.lower() in d.name.lower():
                return d
    
    return path


def main():
    parser = argparse.ArgumentParser(description="Lance un serveur de preview pour un site web")
    parser.add_argument("project", help="Nom du projet ou chemin du dossier")
    parser.add_argument("--port", "-p", type=int, default=8080, help="Port HTTP (défaut: 8080)")
    parser.add_argument("--no-open", action="store_true", help="Ne pas ouvrir le navigateur")
    
    args = parser.parse_args()
    
    project_path = resolve_project_path(args.project)
    
    if not project_path.exists():
        print(f"❌ Projet introuvable : {project_path}")
        sys.exit(1)
    
    serve_dir = find_serve_directory(project_path)
    port = find_free_port(args.port)
    
    if not (serve_dir / "index.html").exists():
        print(f"⚠️  Pas de index.html trouvé dans {serve_dir}")
        print("  Le serveur sera lancé quand même.")
    
    # Changer le répertoire de travail
    os.chdir(serve_dir)
    
    url = f"http://localhost:{port}"
    
    print(f"""
🌐 Serveur de preview démarré !

  📂 Dossier : {serve_dir}
  🔗 URL     : {url}
  🔌 Port    : {port}

  Appuyez sur Ctrl+C pour arrêter.
""")
    
    # Ouvrir le navigateur
    if not args.no_open:
        webbrowser.open(url)
    
    # Gérer Ctrl+C proprement
    def signal_handler(sig, frame):
        print("\n\n✅ Serveur arrêté.")
        sys.exit(0)
    
    signal.signal(signal.SIGINT, signal_handler)
    
    # Lancer le serveur
    server = HTTPServer(("localhost", port), QuietHandler)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n✅ Serveur arrêté.")
        server.shutdown()


if __name__ == "__main__":
    main()
