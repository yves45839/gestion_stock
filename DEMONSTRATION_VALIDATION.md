# Vérifications de la branche de démonstration

- Branche séparée : `test/nouvelle-interface-vente`, basée sur `21a9376`.
- Aucun envoi sur `main` ni déploiement PythonAnywhere.
- Base de démonstration : `var/sales-demo/db.sqlite3`. La base locale normale `db.sqlite3` n'a pas changé, comparaison SHA256 avant/après les essais.
- Lancement `python start_demo.py --prepare-only` vérifié : nouvel environnement `.venv`, installation des dépendances, migrations et préparation des données fictives. La commande de préparation est réexécutable sans dupliquer les factures et produits d'exemple.
- `manage.py check` et absence de migrations manquantes dans le code vérifiées.
- **34 tests ciblés réussis** : références, enregistrements concurrents, conversion, remises %, remises fixes, arrondis, paiements partiels, refus des montants invalides, solde, retours, calculs des analyses et PDF.
- **136 tests exécutés dans la suite complète : 129 réussis, 6 échecs et 1 erreur préexistants**. Les écarts connus concernent la catégorisation, des attentes 200 contre des redirections 302, une permission IA, des périodes d'analyses contradictoires et un test utilisant des champs de modèle inexistants. Aucun nouvel échec détecté.
- Chromium : recherche/scanner, quantités, calcul 37 500 − 3 750 = 33 750, paiement 20 000, reste 13 750, solde 96 250 ; aperçus avec/sans solde, PDF navigateur, bascule vente/devis avec conservation de la saisie, mise en page mobile 390 px sans débordement horizontal et aucune erreur JavaScript.
- PDF WeasyPrint vérifié par lecture de son contenu : remise et solde présents lorsque demandés, solde absent lorsque décoché.

Les essais utilisateurs restent à effectuer. Cette branche n'est pas approuvée pour un déploiement de production ; le guide prévoit une validation avant toute fusion et migration de production.
