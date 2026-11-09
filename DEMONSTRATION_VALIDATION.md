# Vérifications de la branche de démonstration

- Branche séparée : `test/nouvelle-interface-vente`, basée sur `21a9376`.
- Aucun envoi sur `main` ni déploiement PythonAnywhere.
- Base de démonstration : `var/sales-demo/db.sqlite3`. La base locale normale `db.sqlite3` n'a pas changé, comparaison SHA256 avant/après les essais.
- Lancement `python start_demo.py --prepare-only` vérifié : nouvel environnement `.venv`, installation des dépendances, migrations et préparation des données fictives. La commande de préparation est réexécutable sans dupliquer les factures et produits d'exemple.
- `manage.py check` et absence de migrations manquantes dans le code vérifiées.
- **41 tests ciblés réussis** : références, enregistrements concurrents, conversion, remises %, remises fixes, arrondis, paiement intégral automatique et paiements manuels (dont zéro), refus des montants invalides, création différée du client, versionnement automatique des ressources, solde, retours, calculs des analyses et PDF.
- **145 tests exécutés dans la suite complète : 138 réussis, 6 échecs et 1 erreur préexistants**. Les écarts connus concernent la catégorisation, des attentes 200 contre des redirections 302, une permission IA, des périodes d'analyses contradictoires et un test utilisant des champs de modèle inexistants. Aucun nouvel échec détecté.
- Chromium : recherche/scanner, quantités, calcul 37 500 − 3 750 = 33 750, paiement 20 000, reste 13 750, solde 96 250 ; aperçus avec/sans solde, PDF navigateur, bascule vente/devis avec conservation de la saisie, mise en page mobile 390 px sans débordement horizontal et aucune erreur JavaScript.
- PDF WeasyPrint vérifié par lecture de son contenu : remise et solde présents lorsque demandés, solde absent lorsque décoché.
- Ajustements de l'interface : titres réduits, sous-titre de référence et action Ajouter un article retirés, recherche client unique avec proposition de création, libellés Montant hors taxes et Nouveau Solde. Chromium vérifie le paiement automatique, le maintien d'un montant manuel (y compris zéro), l'option de solde dans les aperçus et la recherche client sans seconde barre.
- Régression du sélecteur client reproduite avec l'ancien JavaScript et le nouveau formulaire : aucune proposition et erreur JavaScript. Les URL CSS/JS sont maintenant liées à une empreinte de leur contenu. Chromium vérifie que l'ancienne ressource en cache est évitée, et que la sélection/création au clic et avec Entrée fonctionne pour ventes et devis, jusque dans l'aperçu du document.

Les essais utilisateurs restent à effectuer. Cette branche n'est pas approuvée pour un déploiement de production ; le guide prévoit une validation avant toute fusion et migration de production.

## Notifications des fiches clients — 9 novembre 2026

- Migration `0033_customer_review_notifications` appliquée uniquement à la base de démonstration. Les anciennes fiches ne sont pas marquées à vérifier.
- Badge vert personnel dans **Clients** et filtre **Mes clients à vérifier**. Les créations depuis une vente ou un devis sont signalées après enregistrement ; l'aperçu et les doubles soumissions ne créent pas de notification supplémentaire.
- Confirmation explicite dans les deux formulaires de modification client. Une simple consultation, une sauvegarde sans confirmation ou un formulaire invalide conservent le rappel.
- **41 tests ciblés réussis** (`inventory.test_customer_reviews`, `inventory.test_sale_editor`, `inventory.test_references`). Absence de migration manquante et `git diff --check` vérifiés.
- Chromium : création réelle depuis vente puis devis, compteur **1 → 2 → 1 → 0**, filtrage, sauvegarde sans confirmation, complément et vérification des deux fiches. Affichage vérifié à 1440 px et 390 px, sans débordement horizontal sur mobile ni erreur JavaScript.
- Aucune fusion dans `main`, aucun déploiement de production.
