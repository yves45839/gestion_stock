# Tester les envois avant intégration

Cette version reste sur `test/envoi-documents`. Ne pas fusionner dans `main` et ne pas l’installer sur l’application PythonAnywhere utilisée par les collègues avant validation. Les essais utilisent une base et des fichiers séparés de la production.

## 1. Essayer sans compte Microsoft ou Meta

Télécharger et décompresser la [branche de test](https://github.com/yves45839/gestion_stock/archive/refs/heads/test/envoi-documents.zip), puis ouvrir un terminal dans ce dossier et lancer sous Windows :

```powershell
py -3.12 start_demo.py
```

**Windows et moteur PDF :** le lanceur vérifie d’abord WeasyPrint en générant un petit PDF. Si ses bibliothèques natives ne se chargent pas, il prépare automatiquement Playwright et Chromium dans l’environnement de démonstration, puis vérifie un PDF avant le démarrage. Le premier lancement peut donc télécharger un navigateur et prendre quelques minutes. Aucun outil système Windows n’est à installer manuellement pour ce mode de secours.

Si vous avez téléchargé une ancienne archive et voyez « Le moteur PDF WeasyPrint doit être installé », arrêter le serveur avec **Ctrl+C**, télécharger à nouveau cette branche, décompresser dans un **nouveau dossier** et relancer `py -3.12 start_demo.py`. L’ancien dossier et ses données restent conservés. Avec une copie Git de cette branche, récupérer les changements puis relancer le même lanceur.

Le rendu de secours utilise Chromium : contrôler visuellement la mise en page des documents d’essai. Il est réservé à la démonstration ; la production conserve WeasyPrint. Si le téléchargement ne se termine pas, vérifier l’accès Internet et relancer le lanceur avant de reprendre l’essai.

Ouvrir `http://127.0.0.1:8001/`. Connexion fictive : `demo` / `Demo-Ventes-2026!`. Les données se trouvent exclusivement dans `var/sales-demo/`. Ce serveur de développement est local ; aucune deuxième application PythonAnywhere n’est nécessaire.

1. Choisir le site **Treichville — DÉMO**.
2. Dans **Devis**, ouvrir `DEV-DEMO-EXEMPLE`, puis **Préparer un envoi**.
3. Cocher email, WhatsApp ou les deux. Saisir une adresse et un numéro international, par exemple `+225` suivi du numéro complet. Confirmer l’accord du destinataire WhatsApp.
4. Personnaliser le message, puis cliquer **Préparer et vérifier les pièces jointes**.
5. Télécharger le document depuis l’écran de vérification. Vérifier référence, client, montants et mise en page. Il porte la mention **DOCUMENT DE TEST — données fictives**.
6. Vérifier les destinataires, cocher la confirmation, puis **Simuler l’envoi**. Le résultat doit indiquer **aucun message envoyé** pour chaque canal. Il ne doit rien arriver dans une boîte email ou WhatsApp.
7. Pour une facture, ouvrir l’aperçu PDF d’une vente confirmée depuis **Ventes**, puis **Préparer un envoi**. Un devis non confirmé ne peut pas être envoyé en tant que facture.
8. Dans **Analyses**, choisir la période et le site, puis **Préparer un envoi** dans la section des ventes confirmées. Tester le **PDF**, puis les **images PNG**. Une image est produite par page, sans supprimer les pages suivantes.
9. Retrouver les essais dans **menu utilisateur → Mes essais d’envoi**. Recharger la page ou confirmer deux fois ne doit pas déclencher un second envoi.

Les pièces jointes sont des instantanés : modifier ensuite une vente ne modifie pas les fichiers déjà préparés. Elles sont conservées sous `var/sales-demo/document-delivery/`, hors des médias publics, et accessibles uniquement au créateur connecté, avec contrôle du site. La préparation ne crée ni vente, ni client, ni mouvement de stock.

## 2. Email réel avec Microsoft 365

L’adresse choisie est **info@label-ci.com**. L’intégration utilise **Microsoft Graph et OAuth**, sans mot de passe de messagerie enregistré dans le code. L’existence de la boîte et de ses accès Outlook ne suffit pas à donner une autorisation d’envoi à l’application.

Un administrateur Microsoft 365 doit préparer une application Entra pour LrStock, avec l’autorisation d’application `Mail.Send` et le consentement administrateur. Restreindre côté Exchange l’accès de cette application à la boîte `info@label-ci.com`, avec la politique d’accès ou le mécanisme RBAC pris en charge par votre tenant. Un compte utilisateur avec accès à la boîte ne remplace pas ce réglage.

Renseigner les variables du fichier `config/delivery.env.example` dans un fichier `.env` local, ignoré par Git. Ne jamais transmettre de secret dans la discussion ou le dépôt. Les trois valeurs Microsoft sont l’identifiant du tenant, l’identifiant de l’application et son secret d’application. Un certificat peut être ajouté ultérieurement ; ce prototype utilise un secret dédié, à durée limitée.

Pour les destinataires, définir `LRSTOCK_TEST_EMAILS` avec uniquement les adresses d’essai autorisées, séparées par des virgules. Il est impossible d’envoyer réellement à une autre adresse dans cette version.

Le message demande `LrStock <info@label-ci.com>` comme expéditeur et conserve une copie dans les éléments envoyés. Vérifier dans le premier email reçu l’adresse, le nom affiché, le PDF ou les images, et l’absence de classement en courrier indésirable. Le nom visible peut dépendre de l’annuaire Microsoft et des contacts du destinataire ; configurer aussi le nom de la boîte dans Microsoft si nécessaire.

[Documentation Microsoft Graph sendMail](https://learn.microsoft.com/en-us/graph/api/user-sendmail?view=graph-rest-1.0)

## 3. WhatsApp réel

Les numéros déjà utilisés dans WhatsApp Business ne prouvent pas qu’une application peut envoyer via l’API. Pour les premiers essais, utiliser le numéro de test fourni dans le parcours WhatsApp Cloud API de Meta et ses destinataires de test vérifiés. Le choix et le rattachement d’un numéro professionnel pour LrStock viendront après les essais ; ne pas supprimer un compte WhatsApp existant pour ce prototype.

Configurer le jeton d’accès, l’identifiant du numéro émetteur et une version Graph actuellement prise en charge dans votre tableau de bord Meta. La version n’est pas imposée dans le code. Configurer deux modèles de messages approuvés, dans la langue indiquée :

- Modèle PDF : en-tête **DOCUMENT**, corps `LrStock : {{1}}`.
- Modèle image : en-tête **IMAGE**, corps `LrStock : {{1}}`.

Le seul paramètre du corps contient le message saisi. Les noms exacts des modèles, leur langue et leur structure doivent correspondre à ce qui a été approuvé par Meta. Les modèles permettent un envoi initié par l’entreprise ; les essais n’utilisent pas un lien `wa.me` et ne nécessitent pas de rendre les factures publiques. Le fichier est chargé directement chez Meta, puis référencé par son identifiant média. L’accord du destinataire doit être confirmé.

Définir `LRSTOCK_TEST_PHONES` avec les numéros internationaux autorisés, séparés par des virgules. Utiliser une adresse email et un numéro qui vous appartiennent pour les premiers essais. Les éventuels frais et limites sont ceux du compte Meta ; le mode simulation ne contacte pas Meta.

**Affichage LrStock :** un nom commercial peut être proposé pour le profil, sous réserve des règles de Meta. Ce prototype ne peut pas garantir que le numéro sera masqué. La documentation officielle indique notamment qu’une entreprise qui contacte une personne peut afficher son numéro même avec un nom d’utilisateur.

[FAQ officielle WhatsApp](https://www.whatsapp.com/usernames-faq/) · [Exemples Meta de messages avec pièces jointes](https://www.postman.com/meta/whatsapp-business-platform/request/2qbotrb/send-message-template-media)

## 4. Passer de la simulation aux essais réels

Une fois les connexions configurées et les listes de destinataires remplies, mettre `LRSTOCK_DELIVERY_SIMULATE=false` dans `.env`, puis arrêter et relancer :

```powershell
py -3.12 start_demo.py --delivery-test
```

Le lancement sans `--delivery-test` reste toujours en simulation, même si cette variable vaut `false`. Les paramètres ordinaires de l’application désactivent la fonctionnalité par défaut. Les données restent fictives et isolées avec `--delivery-test` ; seuls les messages deviennent réels.

Préparer un **nouvel** essai après le changement de mode. Un document préparé en simulation ne peut pas être envoyé réellement. L’écran doit annoncer **Essai réel** avant la confirmation. Commencer par un seul canal, puis l’autre, et enfin les deux. Ne pas utiliser de véritables clients avant validation.

Une confirmation fournisseur est présentée comme **Accepté par le prestataire**, jamais comme « reçu » ou « lu ». Microsoft renvoie 202 et Meta un identifiant de message ; les accusés de livraison et de lecture par webhook ne sont pas implémentés dans ce prototype. Si la connexion se coupe après l’envoi, le résultat est incertain et aucun renvoi automatique n’a lieu. Vérifier dans Microsoft/Meta avant de préparer un autre essai. Les résultats sont sauvegardés séparément : un succès email reste visible même si WhatsApp échoue.

## Limites de la version d’essai

- Un seul rapport : ventes confirmées, avec période et site. Les autres rapports viendront après votre validation.
- Devis et factures : PDF. Rapport : PDF ou images PNG, une par page ; jusqu’à 20 pages en mode image. Au-delà, réduire la période ou utiliser le PDF.
- Limite des fichiers générés : 5 Mo par fichier, 15 Mo par préparation. L’email Microsoft utilise l’envoi simple : le contenu JSON encodé est limité à 3 Mo dans ce prototype ; si le rapport est trop lourd, réduire sa période ou choisir le PDF. Pas encore de session d’upload pour les grosses pièces jointes.
- Historique personnel des 50 dernières préparations ; les fichiers sont conservés sur la machine de test. Ils ne sont pas chargés dans un dossier public.
- Pas d’envoi en masse, de relance automatique, de suivi de lecture ou de bascule automatique entre prestataires.
- Aucune configuration d’envoi réelle n’a été testée tant que les accès Microsoft/Meta et les destinataires d’essai n’ont pas été renseignés sur votre machine.

## Vérifications techniques

```bash
python manage.py test inventory.test_delivery inventory.test_profile inventory.tests.TopbarNavigationTests inventory.test_credit_limits inventory.test_customer_reviews inventory.test_sale_editor inventory.test_references test_demo_launcher --settings=config.demo_settings --noinput
python manage.py check
python manage.py makemigrations --check --dry-run --settings=config.demo_settings
```

Migration de la branche : `0035_documentdelivery`, appliquée uniquement à la base de démonstration pour les essais. La migration `0034_userprofile` précédente reste nécessaire si votre copie n’est pas à jour. Ne pas exécuter cette procédure contre la base PythonAnywhere pour essayer la fonctionnalité.
