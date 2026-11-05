# Tester la nouvelle interface sans toucher à PythonAnywhere

Cette version reste sur la branche `test/nouvelle-interface-vente`. Aucun déploiement ni fusion dans `main` n'est nécessaire pour essayer la démonstration.

## Sur votre ordinateur

Installer Python 3.12, puis télécharger et décompresser l'archive de cette branche dans un **nouveau dossier**, ou cloner cette branche dans un dossier distinct. Ne pas utiliser le dossier de production et ne pas recopier son fichier `.env`.

Dans un terminal ouvert dans ce nouveau dossier :

```sh
python start_demo.py
```

Le script prépare un environnement Python, installe les dépendances et crée une base de données fictive. L'adresse locale s'affiche dans le terminal. Laisser ce terminal ouvert pendant vos essais et arrêter avec Ctrl+C.

Sur Windows, si `python` n'est pas reconnu, utiliser `py start_demo.py`. Sur macOS/Linux, utiliser `python3 start_demo.py`. Les dépendances système WeasyPrint nécessaires au téléchargement PDF dépendent de l'ordinateur ; si elles sont absentes, tester les documents avec **Aperçu → Imprimer → Enregistrer au format PDF** dans le navigateur. Le téléchargement PDF WeasyPrint est déjà testé dans l'environnement cloud Linux.

Connexion **uniquement pour cette démonstration locale** : utilisateur `demo`, mot de passe `Demo-Ventes-2026!`.

Les données et les fichiers médias sont dans `var/sales-demo/`, séparés de `db.sqlite3` et de `media/`. Les clés IA sont neutralisées, le panneau IA est désactivé et aucun worker externe n'est lancé. Le serveur écoute uniquement sur votre ordinateur. Ne pas publier cette configuration de développement sur Internet.

## Parcours de validation

1. Sélectionner le site **Treichville — DÉMO** dans l'en-tête.
2. Ouvrir Nouvelle vente et sélectionner **ETS KONÉ DISTRIBUTION**. Son solde initial est de **82 500 FCFA** : deux factures partiellement payées, indépendantes du devis d'exemple.
3. Ajouter `DS-2CD1043` (1), `RJ45-1029` (10) et `CAT6-1M` (2) avec la recherche ou en saisissant leur code puis Entrée.
4. Le total doit être **37 500 FCFA**. Une remise de **10 %** ou **3 750 FCFA** donne un net de **33 750 FCFA**.
5. Saisir un paiement de **20 000 FCFA**. Le reste de cette vente est **13 750 FCFA**, le solde client projeté est **96 250 FCFA**.
6. Activer puis désactiver **Afficher le solde sur le document** et cliquer sur **Aperçu**. L'aperçu s'ouvre dans un autre onglet, sans enregistrer de vente ni modifier le stock. L'impression du navigateur permet d'enregistrer cet aperçu en PDF.
7. Enregistrer une vente, examiner sa facture et télécharger son PDF. Le double enregistrement du même formulaire ne doit pas créer un doublon.
8. Dans Devis, modifier le devis **DEV-DEMO-EXEMPLE**. Vérifier la remise et le solde, puis confirmer : un nouveau numéro VTE remplace le DEV, conservé comme référence d'origine. Un devis non confirmé ne modifie pas le stock ni les impayés.
9. Tester les erreurs : remise supérieure à 100 %, remise fixe dépassant le total, paiement supérieur au net. Aucun document ne doit être créé.

Le solde antérieur imprimé est mémorisé lors de l'enregistrement du document et recalculé lors de la conversion du devis. Cela conserve le contexte financier d'une facture déjà émise. Décocher l'option masque uniquement ce solde sur le document ; les calculs du compte client restent actifs.

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

La migration 0032 est préparée pour cette branche et appliquée à la base de démonstration uniquement. La fusion, la sauvegarde de production et le déploiement seront traités après votre validation. Ne pas fusionner ni déployer cette branche automatiquement.
