"""Launch the isolated demo on your own computer: python start_demo.py."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv


def check_runtime():
    if (3, 10) <= sys.version_info[:2] < (3, 14):
        return
    # Windows often associates `python` with the newest installed version.
    # Prefer the tested 3.12 interpreter without changing system associations.
    launcher = shutil.which("py") if os.name == "nt" else None
    if launcher:
        probe = subprocess.run([launcher, "-3.12", "-c", "import sys"], capture_output=True)
        if probe.returncode == 0:
            raise SystemExit(subprocess.run(
                [launcher, "-3.12", str(Path(__file__).resolve()), *sys.argv[1:]],
            ).returncode)
    raise SystemExit(
        f"Python {sys.version_info.major}.{sys.version_info.minor} détecté. "
        "Ces dépendances nécessitent Python 3.10 à 3.13 ; Python 3.12 est recommandé.\n"
        "Installez Python 3.12 (64 bits) puis relancez : py -3.12 start_demo.py\n"
        "Aucune base de données n'a été modifiée."
    )


def prepare_python(root):
    folder = root / ".venv"
    python = folder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if folder.exists():
        try:
            probe = subprocess.run(
                [str(python), "-c", "import json, sys; print(json.dumps(list(sys.version_info[:2])))"],
                capture_output=True, text=True, check=True,
            )
            version = tuple(json.loads(probe.stdout.strip()))
        except (OSError, subprocess.CalledProcessError, ValueError, TypeError):
            version = None
        if version != tuple(sys.version_info[:2]):
            # Preserve the failed environment. Demo data lives separately in var/.
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = folder.with_name(f".venv-incompatible-{stamp}")
            try:
                folder.rename(backup)
            except OSError as error:
                raise SystemExit(
                    "Fermez les programmes utilisant .venv, puis relancez le script. "
                    "L'ancien environnement n'a pas pu être renommé."
                ) from error
            print(f"Ancien environnement conservé dans {backup.name} ; création avec Python "
                  f"{sys.version_info.major}.{sys.version_info.minor}.", flush=True)
    if not python.exists():
        venv.create(folder, with_pip=True)
    return python


def main():
    parser = argparse.ArgumentParser(description="Démonstration LR STOCK, sans connexion à la production.")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--prepare-only", action="store_true", help="Préparer sans démarrer le serveur")
    parser.add_argument("--delivery-test", action="store_true", help="Essais d’envoi : simulation par défaut, destinataires réels limités à la liste de test")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    check_runtime()
    python = prepare_python(root)
    demo_settings = "config.delivery_test_settings" if args.delivery_test else "config.demo_settings"
    environment = {**os.environ, "DJANGO_SETTINGS_MODULE": demo_settings,
                   "PIP_CACHE_DIR": str(root / "var" / "pip-cache")}
    def run(*command):
        subprocess.run([str(python), *command], cwd=root, env=environment, check=True)
    run("-m", "pip", "install", "-r", "requirements.txt", "--disable-pip-version-check")
    run("manage.py", "prepare_sales_demo", f"--settings={demo_settings}")
    if not args.prepare_only:
        print(f"Ouvrez l’adresse locale http://127.0.0.1:{args.port}/ventes/nouvelle/", flush=True)
        run("manage.py", "runserver", f"127.0.0.1:{args.port}", "--noreload", f"--settings={demo_settings}")


if __name__ == "__main__":
    main()
