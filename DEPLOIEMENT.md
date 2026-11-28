# Déploiement sur Galon Cloud (Compute)

L'application (Django + PostgreSQL + Redis + worker Celery) tourne avec
Docker Compose sur une machine virtuelle Galon Compute.

Taille recommandée : `2vcpu-4gb` sous `ubuntu-24.04` (environ 15,49 $/mois).
La taille `1vcpu-1gb` est trop juste pour PostgreSQL, Celery et WeasyPrint.

## 1. Préparer la machine

```bash
ssh <utilisateur>@<ip-de-la-vm>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
git clone https://github.com/yves45839/gestion_stock.git
cd gestion_stock
```

## 2. Configurer

```bash
cp .env.production.example .env
nano .env   # clé secrète, domaine, mot de passe PostgreSQL, clés API
```

## 3. Lancer

```bash
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Les migrations s'appliquent automatiquement au démarrage du service `web`.
L'application écoute sur le port 8000 (modifiable avec `WEB_PORT` dans `.env`).
Pour HTTPS sur un domaine, placer un proxy (Caddy ou Nginx) devant le port
8000 et laisser `DJANGO_BEHIND_HTTPS_PROXY=True`.

## Mise à jour

```bash
git pull && docker compose up -d --build
```

## Reprendre les données existantes (SQLite → PostgreSQL)

Sur l'ancien serveur :
```bash
python manage.py dumpdata --natural-foreign --natural-primary \
  -e contenttypes -e auth.permission -o data.json
```
Copier `data.json` et le dossier `media/` sur la VM, puis :
```bash
docker compose cp data.json web:/app/data.json
docker compose exec web python manage.py loaddata /app/data.json
docker compose cp media/. web:/app/media/
```

## Sauvegardes

```bash
docker compose exec db pg_dump -U gestion_stock gestion_stock > sauvegarde.sql
```
