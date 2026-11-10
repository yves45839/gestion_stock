# Vérifications de la branche de démonstration

## Validation pour intégration — 9 novembre 2026

L’utilisateur a autorisé l’intégration des modifications de `test/nouvelle-interface-vente` dans `main`, jusqu’au correctif `791939a` (alertes rouges). Intégration sans conflit depuis `21a9376` ; 53 tests ciblés réussis, vérification Django avec la configuration normale et absence de migrations manquantes vérifiées. Aucun déploiement sur PythonAnywhere ni migration de sa base de production effectués.

## Historique des essais avant cette validation

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

À ce stade des essais, la branche attendait la validation utilisateur. L’autorisation d’intégration dans `main` est consignée ci-dessus ; le déploiement de production reste une étape distincte.

## Notifications des fiches clients — 9 novembre 2026

- Migration `0033_customer_review_notifications` appliquée uniquement à la base de démonstration. Les anciennes fiches ne sont pas marquées à vérifier.
- Badge vert personnel dans **Clients** et filtre **Mes clients à vérifier**. Les créations depuis une vente ou un devis sont signalées après enregistrement ; l'aperçu et les doubles soumissions ne créent pas de notification supplémentaire.
- Confirmation explicite dans les deux formulaires de modification client. Une simple consultation, une sauvegarde sans confirmation ou un formulaire invalide conservent le rappel.
- **41 tests ciblés réussis** (`inventory.test_customer_reviews`, `inventory.test_sale_editor`, `inventory.test_references`). Absence de migration manquante et `git diff --check` vérifiés.
- Chromium : création réelle depuis vente puis devis, compteur **1 → 2 → 1 → 0**, filtrage, sauvegarde sans confirmation, complément et vérification des deux fiches. Affichage vérifié à 1440 px et 390 px, sans débordement horizontal sur mobile ni erreur JavaScript.
- Aucune fusion dans `main`, aucun déploiement de production.

## Alertes de crédit — 9 novembre 2026

- Préavis dès 80 %, états distincts atteint/dépassé et plafond nul sans crédit autorisé. Information sans blocage, sur les écrans internes uniquement.
- Dette calculée sur les restes dus des factures confirmées après remises, règlements et retours. Projection distincte pour les ventes et les devis, avertissement avant et après conversion d’un devis.
- **49 tests ciblés réussis** (`inventory.test_credit_limits`, `inventory.test_customer_reviews`, `inventory.test_sale_editor`, `inventory.test_references`). Seuils exacts, plafond nul, exclusion des devis, factures payées, retours, remises, absence de blocage, notifications serveur et limitation des requêtes sur la liste clients vérifiés. Aucune nouvelle migration nécessaire.
- Chromium : plafond 100 000 FCFA, impayés 82 500 FCFA, état proche puis atteint et dépassé selon le paiement. Recalcul après remise, paiement automatique, changement de client et simulation de devis vérifiés. Liste et fiche client, affichages 1440 px et 390 px, aucune erreur JavaScript ni débordement horizontal sur mobile.
- Modifications réservées à `test/nouvelle-interface-vente`, aucun déploiement de production.

## Bandeau de navigation validé — 10 novembre 2026

L’utilisateur a demandé d’appliquer la maquette validée : bandeau bleu existant à deux niveaux, onglets avec icônes et onglet actif blanc, nom du site avec localisation, cloche dans un bouton assorti, puis séparateur et avatar générique avec nom de l’utilisateur. Les noms affichés proviennent du site actif et du compte connecté, sans imposer « Treichville » ou « Yves ».

Tous les accès existants restent disponibles ; Analyses, Inventaire physique, Valorisation, IA (personnel autorisé) et Import sont regroupés dans « Plus ». Le menu utilisateur conserve Nouvelle vente, Mouvement et la déconnexion par POST avec CSRF. La cloche renvoie aux fiches clients personnelles à vérifier via le filtre existant `?review=1` ; elle ne supprime pas les rappels. Les employés gardent un site fixe et seuls les superutilisateurs disposent du sélecteur.

Validation : 61 tests ciblés réussis (navigation, plafonds de crédit, fiches clients, éditeur, références et lanceur), `manage.py check`, absence de migrations manquantes et `git diff --check`. Chromium : sélection du site, onglet actif, menus, fermeture par Échap et clic extérieur, absence d’erreurs JavaScript et de débordement du bandeau et des menus aux largeurs 320, 375, 390, 600, 768, 960 et 1440 pixels. Les essais utilisent exclusivement la base de démonstration isolée.

Aucune migration supplémentaire. Sur PythonAnywhere : récupérer `main`, exécuter `python manage.py collectstatic --noinput` dans l’environnement virtuel de l’application, puis **Web → Reload** et actualiser le navigateur. Le rechargement est nécessaire pour charger les modèles HTML. Le déploiement PythonAnywhere reste distinct de l’envoi GitHub ; il n’a pas été réalisé depuis cet environnement.

## Bandeau compact et photo de profil — 10 novembre 2026

Première ligne réduite de 90 à 59 px sur ordinateur (−34,4 % mesurés dans Chromium). Le nom de connexion est affiché seul, au lieu du nom complet ; aucun site n’est ajouté au nom du compte.

Le menu utilisateur propose « Photo de profil » : chargement, remplacement ou suppression de sa propre photo. Formats JPG, PNG et WebP, maximum 5 Mo et 20 millions de pixels. L’image est réencodée en JPEG, sans métadonnées, limitée à 512 px et enregistrée sous un nom aléatoire dans `MEDIA_ROOT/profiles/`. Une icône reste affichée sans photo. Les anciennes photos sont supprimées après validation de l’enregistrement. Aucun compte n’est créé ou modifié par la consultation du formulaire.

Migration requise : `0034_userprofile`. Sur PythonAnywhere, activer l’environnement virtuel puis récupérer `main`, exécuter `python manage.py migrate`, `python manage.py collectstatic --noinput` et `python manage.py check`, puis **Web → Reload**. Les photos utilisent la configuration des médias déjà employée pour les images des produits.

Validation : 68 tests ciblés réussis, contrôles Django et migrations, `git diff --check`. Chromium : hauteur mesurée 59 px, chargement réel d’une photo, affichage dans le bandeau après changement de page, suppression et retour à l’icône, menus utilisateur accessibles sans débordement de 320 à 1440 px. Essais et migration limités à la démonstration isolée ; production PythonAnywhere non modifiée depuis cet environnement.
