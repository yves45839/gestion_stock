# Gestion Stock

## Mise a jour des couts produits depuis Excel

1. Installer la dependance si necessaire :
   ```
   pip install openpyxl
   ```
2. Lancer la commande en visant le fichier Excel, par exemple :
   ```
   python manage.py update_product_costs "C:\Users\PC MARKET\Downloads\Produit (product.template) (1).xlsx"
   ```
3. Le script detecte les colonnes de reference et de cout, mais on peut forcer une colonne en utilisant :
   ```
   --reference-column "Référence interne" --cost-column "Coût"
   ```
4. Utiliser `--match-field sku` si la colonne contient les SKU plutot que manufacturer_reference.

## Import des clients depuis Excel

1. Installer la dependance si necessaire :
   ```
   pip install openpyxl
   ```
2. Importer les clients depuis un fichier Excel :
   ```
   python manage.py import_customers "C:\Users\PC MARKET\Downloads\Contact (res.partner).xlsx"
   ```
3. La commande detecte automatiquement les colonnes (nom, telephone, email, ville, pays, vendeur, activite) et cree les fiches client si le nom est renseigne.

## Dependances

- `openpyxl` (lecture des fichiers `.xlsx`)

## Bot IA pour enrichir les produits

1. Installe les dépendances (inclut le client officiel `mistralai`) :
   ```
   pip install -r requirements.txt
   ```
2. Configure `.env` avec `REDIS_URL`, `MISTRAL_API_KEY` et (optionnellement) `MISTRAL_MODEL` (`mistral-medium-latest` par défaut) ou `MISTRAL_AGENT_ID` si tu veux appeler un agent Mistral existant.
3. Le worker IA s'exécute désormais automatiquement dans l'application : tu n'as plus besoin de taper `celery -A config worker --loglevel=info` tant que tout tourne sous Django. Si tu veux malgré tout utiliser Celery pour une raison particulière, le task `generate_product_assets` est toujours disponible.
4. Enfile les produits à enrichir :
   ```
   python manage.py product_asset_bot
   ```
   Utilise `--limit`, `--assets=description,images,techsheet,pdf,videos,blog` ou `--force-*` pour adapter la sélection.
5. Le bot utilise Mistral pour générer les descriptions (courte + longue), la fiche technique JSON et les brouillons de blog, récupère les images via `PRODUCT_BOT_IMAGE_URL_TEMPLATE` (utilise de préférence `{reference}` pour viser la vraie image du produit), et prépare des liens vidéo (YouTube/Vimeo) sous forme de recherches. Les placeholders sont désactivés par défaut (active `PRODUCT_BOT_ALLOW_PLACEHOLDERS=true` si besoin).
6. (Optionnel) Pour chercher des images via APIs: configure de preference Serper (`SERPER_API_KEY`, `PRODUCT_BOT_SERPER_IMAGE_SEARCH_ENABLED=true`) qui est essaye en priorite. Tu peux aussi activer Google Custom Search (`GOOGLE_CUSTOM_SEARCH_API_KEY`, `GOOGLE_CUSTOM_SEARCH_ENGINE_ID`, `PRODUCT_BOT_GOOGLE_IMAGE_SEARCH_ENABLED=true`, `PRODUCT_BOT_GOOGLE_IMAGE_DAILY_LIMIT`) en fallback.
7. Le bot valide maintenant automatiquement les images telechargees : taille minimale (`PRODUCT_BOT_IMAGE_MIN_WIDTH`, `PRODUCT_BOT_IMAGE_MIN_HEIGHT`, `PRODUCT_BOT_IMAGE_MIN_BYTES`), variabilite visuelle anti-placeholder, puis verification OCR (activee avec `PRODUCT_BOT_IMAGE_OCR_ENABLED=true`) pour confirmer la pertinence par rapport au nom/SKU/marque du produit. Installe aussi le binaire Tesseract sur la machine pour activer OCR (`sudo apt-get install tesseract-ocr tesseract-ocr-fra`).

**Mode local instantané**  
Si tu veux que le bot s'exécute immédiatement dans la même requête sans passer par la file (par exemple pour des tests rapides), active `PRODUCT_BOT_INLINE_RUN=true` dans `.env` ou lance `python manage.py product_asset_bot --inline`. Le formulaire IA réagit de la même façon : les descriptions/images sont générées sur-le-champ et les messages remontent directement dans l'interface.


## Agent IA de contrôle qualité produits

Pour obtenir des fiches produits plus riches (dans l'esprit e-commerce avancé), lance l'agent qualité :
```
python manage.py run_product_quality_agent --threshold 75 --limit 100
```

Ce que fait l'agent :
- calcule un score qualité (/100) par produit (titres, descriptions courte/longue, specs techniques, image, contenus annexes) ;
- détecte les fiches faibles (score < seuil) ;
- déclenche automatiquement l'enrichissement IA (Mistral) sur descriptions/specs/vidéos puis recalcule le score.

Options utiles :
- `--product-id 123` : traiter un seul produit ;
- `--threshold 80` : exiger une qualité plus élevée ;
- `--limit 20` : limiter le volume par lot.

## Attribution automatique des categories

Utilise la commande suivante pour attribuer des categories selon des mots-cles trouves dans le SKU/nom :
```
python manage.py auto_assign_categories
```

Options utiles :
- `--all` : remplace la categorie pour tous les produits (sinon seulement les non classes).
- `--limit 200` : limite le nombre de produits traites.
- `--dry-run` : affiche les changements sans sauvegarder.
- `--rules path\\to\\category_rules.json` : charge un fichier de regles JSON.

Exemple de fichier `category_rules.json` :
```
{
  "default_category": "Non classe",
  "rules": [
    { "category": "Camera", "keywords": ["camera", "cam", "dome", "bullet"], "regex": ["\\bptz\\b"] },
    { "category": "Switch", "keywords": ["switch", "poe", "ethernet"] }
  ]
}
```
