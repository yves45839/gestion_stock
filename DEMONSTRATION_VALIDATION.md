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

## Envois de documents : branche de test — 10 novembre 2026

Demande utilisateur : tester les envois par email et/ou WhatsApp avant intégration définitive. Branche isolée `test/envoi-documents`, sans fusion dans `main`. Premier rapport retenu : ventes confirmées. La boîte `info@label-ci.com` est hébergée chez Microsoft ; des numéros WhatsApp Business existent, mais aucun n’est encore choisi pour LrStock.

Fonctionnalités : préparation et confirmation séparées, devis/factures PDF, rapport PDF ou PNG par page, instantanés privés, historique personnel, contrôle du site, simulation par défaut et listes obligatoires de destinataires pour les essais réels. Intégrations Microsoft Graph OAuth et Meta Cloud API avec modèles approuvés. Aucun lien public vers une facture. Prévention des doubles confirmations et conservation des succès partiels. Les confirmations prestataires sont distinguées de la réception réelle.

Validation : 88 tests ciblés réussis, puis 21 tests spécifiques aux envois réussis après ajout du cas de modèle image WhatsApp (89 tests distincts au total). Contrôles Django, absence de migrations manquantes, `git diff --check`. Chromium : téléchargement d’un devis PDF et d’un rapport PNG, simulation email + WhatsApp, confirmation et résultats, absence d’erreurs JavaScript, affichage de 320 à 1440 px. Un cas de rapport de trois pages vérifie que les trois images sont conservées. Les tests des prestataires utilisent des réponses simulées ; aucun email ni message WhatsApp réel n’a été envoyé.

Migration `0035_documentdelivery` appliquée exclusivement à la démonstration. Dépendance de conversion d’image : `pypdfium2==4.30.0`. Guide détaillé et configuration sans secrets : `TEST_ENVOIS.md`, `config/delivery.env.example`. Le lanceur conserve la simulation sans option ; `--delivery-test` permet des essais réels sur données fictives uniquement après configuration explicite.

La configuration Microsoft Entra, les modèles/identifiants Meta et les destinataires de test restent à renseigner chez l’utilisateur. Le nom d’affichage LrStock et la réception réelle ne sont pas validés. Aucun changement sur PythonAnywhere ni publication de ces essais sur `main`.

## Correction du moteur PDF sur Windows — 10 novembre 2026

L’utilisateur a signalé le message « Le moteur PDF WeasyPrint doit être installé » pendant la démonstration Windows. Le contrôle initial confondait un paquet Python absent et des bibliothèques natives impossibles à charger. Le lanceur teste désormais une génération réelle ; en cas d’échec, il installe les dépendances de `requirements-demo-pdf.txt`, prépare Chromium et vérifie un PDF avant le démarrage. Un environnement de démonstration avec moteur forcé utilise également ce secours si l’import WeasyPrint fonctionne mais que son rendu échoue.

Le secours est strictement limité à `IS_SALES_DEMO`. Il traite les pièces jointes d’envoi, les exports PDF habituels des documents et le rapport PDF. Les modèles restent identiques ; le contenu n’est envoyé à aucun service de conversion, JavaScript et les chargements externes sont désactivés dans le navigateur. Les erreurs de préparation indiquent de relancer `start_demo.py` au lieu d’exiger simplement une installation pip.

Validation : 97 tests ciblés réussis, plus le test existant de l’export PDF natif du rapport ; contrôle Django et `git diff --check` réussis. Tests avec WeasyPrint remplacé par `None`, rendu réel Chromium d’un devis PDF et d’un rapport PNG, exports habituels, cas d’import natif réussi avec secours forcé, garde interdisant ce secours en production et tests du lanceur. Les parcours automatisés simulent le défaut de bibliothèques natives sur l’exécuteur Linux ; un essai sur la machine Windows de l’utilisateur reste nécessaire.

## Diagnostic du mode réel et changement d’expéditeur — 10 novembre 2026

L’utilisateur utilise désormais un autre compte Microsoft Entra et une autre boîte ; il ne voit pas le bandeau « Essai réel ». Le mode dépend de `.env` (`LRSTOCK_DELIVERY_SIMULATE=false`) et du lancement avec `--delivery-test`, indépendamment de la configuration Entra. Les valeurs de mode sont maintenant nettoyées des espaces.

L’expéditeur est configurable via `LRSTOCK_EMAIL_SENDER` (défaut historique `info@label-ci.com`). La boîte est mémorisée à la préparation, affichée sur les deux écrans et utilisée dans le contenu Microsoft Graph ainsi que dans le chemin `users/.../sendMail`. Un changement d’expéditeur entre préparation et confirmation exige une nouvelle préparation. L’application Entra et ses permissions doivent correspondre au tenant de cette boîte.

Le lanceur affiche automatiquement `delivery_status` : nom du fichier `.env`, mode, expéditeur, nombre de destinataires de test et présence de paramètres Microsoft. La commande signale `.env.txt` et ne montre aucune valeur de secret, ni ne contacte Microsoft/Meta. Une ancienne préparation conserve son mode d’origine.

Validation : 32 tests ciblés réussis, contrôle Django et `git diff --check`. Chromium vérifie l’affichage « Essai réel », un expéditeur fictif configurable et l’écran de confirmation d’une nouvelle préparation ; aucune confirmation d’envoi réel effectuée. Aucun changement de base ni migration supplémentaire ; `main` et PythonAnywhere restent inchangés.

## Outlook personnel après changement de compte — 10 novembre 2026

La nouvelle boîte indiquée est une adresse Outlook.com personnelle. Elle ne peut pas employer le flux applicatif Microsoft 365 préparé initialement. Ajout du mode `LRSTOCK_MS_AUTH_MODE=personal`, réservé à la démonstration : autorisation déléguée par code sur le site officiel Microsoft, permissions `Mail.Send` et `User.Read`, sans secret client. La commande `connect_outlook` ne déclenche aucun envoi.

Le compte connecté est comparé à `LRSTOCK_EMAIL_SENDER` via `/me` avant enregistrement. La connexion est liée à l’adresse et à l’ID client, stockée dans un fichier privé sous `var/sales-demo/`, ignoré par Git, avec écriture atomique et permissions 0600 sur les systèmes compatibles. Renouvellement de la connexion si possible ; demande de reconnexion si expiration/révocation. L’envoi utilise `/me/sendMail` en mode personnel ; le mode Microsoft 365 existant garde son flux applicatif et sa boîte configurée. Le diagnostic affiche le type de connexion et son état local sans révéler les jetons.

Validation : 115 tests ciblés réussis, contrôle Django, absence de migrations manquantes et `git diff --check`. Tests de consentement, refus, expiration du code, erreur de compte, vérification de portée Mail.Send, cache lié au compte et au client, renouvellement sans secret, blocage hors démonstration et envoi délégué simulé. Aucun code d’authentification ni accès réel à une boîte personnelle obtenu ; réception réelle à tester par l’utilisateur. Aucun changement sur `main` ou PythonAnywhere.
