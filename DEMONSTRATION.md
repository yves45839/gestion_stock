# Tester la nouvelle interface sans toucher à PythonAnywhere

Pour les essais email et WhatsApp de la branche `test/envoi-documents`, suivre [TEST_ENVOIS.md](TEST_ENVOIS.md). Aucun envoi réel n’est activé par défaut.

La nouvelle interface et ses ajustements ont été validés pour intégration dans `main` le 9 novembre 2026. La branche `test/nouvelle-interface-vente` reste disponible pour les essais. Le lanceur de démonstration utilise toujours une base isolée ; le déploiement sur PythonAnywhere est une étape distincte.

## Sur votre ordinateur

Installer Python 3.12, puis télécharger et décompresser l'archive de cette branche dans un **nouveau dossier**, ou cloner cette branche dans un dossier distinct. Ne pas utiliser le dossier de production et ne pas recopier son fichier `.env`.

Dans un terminal ouvert dans ce nouveau dossier :

```sh
python start_demo.py
```

Le script prépare un environnement Python, installe les dépendances et crée une base de données fictive. L'adresse locale s'affiche dans le terminal. Laisser ce terminal ouvert pendant vos essais et arrêter avec Ctrl+C.

Sur Windows, utiliser de préférence **`py -3.12 start_demo.py`** pour sélectionner explicitement Python 3.12. Sur macOS/Linux, utiliser `python3.12 start_demo.py`. Le lanceur refuse Python 3.14, incompatible avec les versions de Pillow et de Pydantic du projet. Si un ancien `.venv` utilise une autre version, il est conservé sous un nom `.venv-incompatible-…`, puis recréé ; les données fictives dans `var/sales-demo` restent conservées.

Si l'installation échoue sur `pillow` / `pydantic-core` / `maturin`, vérifier les versions avec `py -0p`, installer **Python 3.12 64 bits** depuis python.org si nécessaire, puis exécuter `py -3.12 start_demo.py`. Il n'est pas nécessaire d'installer Rust ni de contourner la vérification de compatibilité PyO3.

Les dépendances système WeasyPrint nécessaires au téléchargement PDF dépendent de l'ordinateur ; si elles sont absentes, tester les documents avec **Aperçu → Imprimer → Enregistrer au format PDF** dans le navigateur. Le téléchargement PDF WeasyPrint est déjà testé dans l'environnement cloud Linux.

Connexion **uniquement pour cette démonstration locale** : utilisateur `demo`, mot de passe `Demo-Ventes-2026!`.

### Mettre à jour une démonstration déjà lancée

Arrêter l'ancien serveur avec **Ctrl+C** dans son terminal avant de lancer une nouvelle version. Télécharger l'archive complète de la branche de test, l'extraire dans un nouveau dossier, puis lancer `py -3.12 start_demo.py` depuis ce dossier. Recharger ensuite la page du navigateur avec **Ctrl+F5**. Les fichiers de l'éditeur portent maintenant une version liée à leur contenu pour éviter de mélanger un ancien script en cache avec le nouveau formulaire.

Dans la recherche client, cliquer sur une proposition ou appuyer sur **Entrée** pour la choisir. Pour un nom absent, choisir **Créer le client** : la fiche sera créée lors de l'enregistrement de la vente ou du devis, jamais au simple aperçu.

Après cet enregistrement, un badge vert sur **Clients** indique le nombre de fiches que vous avez créées depuis une vente ou un devis et qui restent à vérifier. Dans **Clients → Mes clients à vérifier**, ouvrir une fiche, compléter les informations disponibles puis cocher **J’ai complété et vérifié les informations du client** et enregistrer. Le compteur diminue après cette confirmation ; consulter la fiche ou enregistrer sans cocher conserve le rappel. Les clients déjà existants et les créations directement depuis **Nouveau client** ne sont pas ajoutés à ce compteur. La migration `0033_customer_review_notifications` est appliquée automatiquement par le lanceur à la base de démonstration.

### Alertes de plafond de crédit

Le plafond défini dans la fiche client déclenche une alerte à **80 %**, puis un avertissement distinct quand il est **atteint** ou **dépassé**. **0 FCFA signifie qu’aucun crédit n’est autorisé** : toute dette positive déclenche l’alerte. Les alertes informent sans bloquer la vente.

Le calcul utilise les restes dus des factures confirmées, avec remises, règlements et retours ; les devis sont exclus de la dette actuelle. L’alerte apparaît dans la liste et la fiche client, dans l’éditeur vente/devis et après enregistrement ou conversion. L’éditeur distingue la situation actuelle du solde projeté après la vente ; pour un devis, la projection suppose une confirmation sans règlement. Le paiement et la remise mettent la projection à jour immédiatement. L’alerte est destinée à l’utilisateur de l’application et ne figure pas sur les documents imprimés.

Exemple d’essai : attribuer **100 000 FCFA** au client fictif ETS KONÉ DISTRIBUTION, dont les impayés sont de **82 500 FCFA**. L’alerte « bientôt atteint » apparaît. Ajouter **35 000 FCFA** à une vente : paiement intégral, aucun crédit supplémentaire ; paiement de **17 500 FCFA**, plafond atteint ; paiement zéro, plafond dépassé de **17 500 FCFA**.

Les données et les fichiers médias sont dans `var/sales-demo/`, séparés de `db.sqlite3` et de `media/`. Les clés IA sont neutralisées, le panneau IA est désactivé et aucun worker externe n'est lancé. Le serveur écoute uniquement sur votre ordinateur. Ne pas publier cette configuration de développement sur Internet.

## Parcours de validation

1. Sélectionner le site **Treichville — DÉMO** dans l'en-tête.
2. Ouvrir Nouvelle vente et rechercher **ETS KONÉ DISTRIBUTION** dans l'unique barre client, puis sélectionner le résultat. Son solde initial est de **82 500 FCFA** : deux factures partiellement payées, indépendantes du devis d'exemple. Pour un client absent, saisir son nom puis choisir **Créer le client** ; sa fiche n'est créée qu'à l'enregistrement.
3. Ajouter `DS-2CD1043` (1), `RJ45-1029` (10) et `CAT6-1M` (2) avec la recherche ou en saisissant leur code puis Entrée.
4. Le **Montant hors taxes** doit être **37 500 FCFA**. Une remise de **10 %** ou **3 750 FCFA** donne un net de **33 750 FCFA**. Le paiement est intégral par défaut et suit les modifications du net à payer.
5. Remplacer le paiement par **20 000 FCFA**. Le reste de cette vente est **13 750 FCFA**, le nouveau solde est **96 250 FCFA**. Ce montant saisi manuellement, y compris zéro, est conservé lorsque les articles ou la remise changent. **Tout régler** réactive le paiement intégral automatique. Un devis n'encaisse rien avant sa confirmation.
6. Activer puis désactiver **Afficher le solde sur le document** et cliquer sur **Aperçu**. L'aperçu s'ouvre dans un autre onglet, sans enregistrer de vente ni modifier le stock. L'impression du navigateur permet d'enregistrer cet aperçu en PDF.
7. Enregistrer une vente, examiner sa facture et télécharger son PDF. Le double enregistrement du même formulaire ne doit pas créer un doublon.
8. Dans Devis, modifier le devis **DEV-DEMO-EXEMPLE**. Vérifier la remise et le solde, puis confirmer : un nouveau numéro VTE remplace le DEV, conservé comme référence d'origine. Un devis non confirmé ne modifie pas le stock ni les impayés.
9. Tester les erreurs : remise supérieure à 100 %, remise fixe dépassant le total, paiement supérieur au net. Aucun document ne doit être créé.

Le solde antérieur imprimé est mémorisé lors de l'enregistrement du document et recalculé lors de la conversion du devis. Cela conserve le contexte financier d'une facture déjà émise. Décocher l'option masque le solde antérieur et le **Nouveau Solde** sur le document ; les calculs du compte client restent actifs. Le reste de la facture courante est une information distincte.

Les essais restent enregistrés dans la base de démonstration entre deux démarrages. Pour retrouver les données initiales, arrêter le serveur, renommer **uniquement** `var/sales-demo` en `var/sales-demo-sauvegarde`, puis relancer le script. Ne jamais copier la base de démonstration vers la production.

## Dans l'environnement cloud déjà préparé

```sh
export XDG_CACHE_HOME=/workspace/.cache
cd /workspace/gestion_stock
/workspace/.venvs/gestion_stock/bin/python manage.py prepare_sales_demo --settings=config.demo_settings
/workspace/.venvs/gestion_stock/bin/python manage.py runserver 127.0.0.1:8001 --noreload --settings=config.demo_settings
```

Cette adresse est interne à la machine cloud ; elle n'est pas une URL publique utilisable depuis votre ordinateur. Le lancement local décrit ci-dessus permet vos essais interactifs sans deuxième application PythonAnywhere.

Tests automatisés :

```sh
python manage.py test inventory.test_sale_editor inventory.test_references inventory.tests.SalesWorkflowTests --noinput
```

Les migrations 0032 et 0033 ont été appliquées à la base de démonstration uniquement pendant les essais. Avant un déploiement PythonAnywhere, sauvegarder la base de production, récupérer `main`, appliquer les migrations avec la configuration de production et actualiser les fichiers statiques, puis recharger l’application web. L’intégration dans `main` ne réalise pas ces opérations.
