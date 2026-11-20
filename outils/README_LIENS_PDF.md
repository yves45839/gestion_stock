# Partager les copies PDF identiques sans changer leurs chemins

Le diagnostic a confirmé la disponibilité des liens physiques sur le compte PythonAnywhere. Un partage de données identiques peut réduire l'espace occupé sans changer les noms des fichiers, le contenu des documents ou les références de la base.

Cette étape est indépendante de la compression : les gains ne doivent pas être additionnés deux fois. Elle ne garantit pas à elle seule le retour sous le quota.

## Préparer une simulation depuis le PC

Télécharger `partager_pdf_identiques.py` dans un dossier de votre ordinateur. Garder la sauvegarde `Téléchargements/LR-PDF/originaux` et le fichier `rapport.json` produits par l'outil de compression.

Exécuter sur le PC :

```powershell
py -3.12 .\partager_pdf_identiques.py
```

Le programme ne se connecte à aucun serveur. Il lit le rapport local et prépare `Téléchargements/LR-PDF/commande-liens-simulation.txt`. La commande contient une empreinte de l'ensemble des chemins et des contenus sauvegardés. Elle est en clair et peut être relue.

Copier son contenu :

```powershell
Get-Content "$env:USERPROFILE\Downloads\LR-PDF\commande-liens-simulation.txt" -Raw -Encoding UTF8 | Set-Clipboard
```

Coller dans la console Bash de PythonAnywhere. Le code s'exécute depuis l'entrée de la console : il n'est pas nécessaire de téléverser un script ou une archive sur le compte plein. La simulation ne crée aucun lien et ne modifie aucun PDF ni aucune donnée de base.

Si les chemins ou les contenus ne correspondent plus à la sauvegarde locale, le programme s'arrête avant tout remplacement. Si les permissions ou les systèmes de fichiers diffèrent, les copies concernées sont ignorées.

## Application après vérification de la simulation

Après vérification et décision d'appliquer, préparer la commande sur le PC :

```powershell
py -3.12 .\partager_pdf_identiques.py --appliquer
```

Le fichier produit est cette fois `commande-liens-appliquer.txt`. Le programme PC n'exécute toujours aucune opération distante. Son contenu doit être relu puis lancé manuellement dans la console Bash appropriée.

Le remplacement utilise un lien temporaire et un renommage atomique. Une erreur de création du lien conserve le chemin d'origine. Les liens déjà traités sont reconnus, ce qui permet de reprendre une application partielle. Les octets réellement libérés dépendent des liens qui existaient auparavant et des fichiers encore ouverts.

**Important : un lien physique partage le même contenu.** Une modification en place d'un de ces PDF modifierait aussi ses autres noms. Tout remplacement ultérieur, notamment par une version compressée, doit passer par un nouveau fichier et un renommage, et non ouvrir le PDF existant en écriture. Ne pas lancer Ghostscript avec le même chemin pour l'entrée et la sortie. Les copies indépendantes sur le PC restent la sauvegarde de référence.

Ce mécanisme ne supprime aucune ligne de la base et ne modifie pas son historique. Il ne remplace pas la vérification du contenu des documents attribués aux produits.

## Vérifications

```sh
python -m unittest discover -s outils -p 'test_partager_pdf_identiques.py' -v
```

Les tests couvrent la simulation, la conservation des noms et des contenus, l'intégrité de la sauvegarde, les reprises, les permissions, les liens symboliques, les échecs de création/renommage et la préparation de commandes sans injection de données du rapport.
