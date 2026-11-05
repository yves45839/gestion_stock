"""Launch the isolated demo on your own computer: python start_demo.py."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import venv


def main():
    parser = argparse.ArgumentParser(description="Démonstration LR STOCK, sans connexion à la production.")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--prepare-only", action="store_true", help="Préparer sans démarrer le serveur")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 ou plus récent est nécessaire (3.12 recommandé).")
    folder = root / ".venv"
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        venv.create(folder, with_pip=True)
    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": "config.demo_settings",
                   "PIP_CACHE_DIR": str(root / "var" / "pip-cache")}
    def run(*command):
        subprocess.run([str(python), *command], cwd=root, env=environment, check=True)
    run("-m", "pip", "install", "-r", "requirements.txt", "--disable-pip-version-check")
    run("manage.py", "prepare_sales_demo", "--settings=config.demo_settings")
    if not args.prepare_only:
        print(f"Ouvrez l’adresse locale http://127.0.0.1:{args.port}/ventes/nouvelle/", flush=True)
        run("manage.py", "runserver", f"127.0.0.1:{args.port}", "--noreload", "--settings=config.demo_settings")


if __name__ == "__main__":
    main()
