# Compression des PDF sur votre ordinateur

Cet outil s'exécute sur **votre ordinateur Windows**, pas dans la console PythonAnywhere.
Python 3.12 et Ghostscript Windows 64 bits sont les seuls prérequis. Aucun paquet Python supplémentaire n'est nécessaire.

Il effectue uniquement des requêtes GET vers le catalogue public existant `/api/products/` et vers les PDF du même site dans `/media/products/datasheets/`. Aucun accès administrateur, mot de passe ou changement du site n'est requis. Si le flux n'est pas accessible ou a changé de format, l'outil s'arrête : ne lui fournir aucun identifiant ni cookie.

## Premier essai : cinq PDF

Télécharger le fichier `compresser_pdf_local.py`, puis ouvrir PowerShell dans son dossier :

```powershell
py -3.12 .\compresser_pdf_local.py --output "$env:USERPROFILE\Downloads\LR-PDF" --limit 5
```

Ou, pour choisir le fichier avec une fenêtre sans avoir à saisir son chemin :

```powershell
& {
    Add-Type -AssemblyName System.Windows.Forms
    $choix = New-Object System.Windows.Forms.OpenFileDialog
    $choix.Title = "Choisir compresser_pdf_local.py"
    $choix.Filter = "Outil Python|compresser_pdf_local.py"
    if ($choix.ShowDialog() -eq "OK") {
        py -3.12 $choix.FileName --output "$env:USERPROFILE\Downloads\LR-PDF" --limit 5
    }
}
```

Le site par défaut est `https://samr.pythonanywhere.com`. Pour un autre site, ajouter `--site https://votre-site`.

## Ce qui est créé sur le PC

Dans `Téléchargements\LR-PDF` :

- `originaux` : sauvegardes intactes des PDF récupérés, avec empreintes SHA256 dans le rapport.
- `compresses` : versions moins volumineuses, au réglage `/ebook` validé sur le premier document. Les chemins et noms sont conservés.
- `rapport.csv` : tailles, gains et états, lisible dans Excel.
- `rapport.json` : chemins d'origine, empreintes des deux versions et résultats des vérifications.

Le traitement est séquentiel. Il rejette les téléchargements HTML, les erreurs Ghostscript (même si le programme retourne 0), les fichiers vides et les sorties dont le nombre de pages diffère de l'original. Une version plus volumineuse n'est pas conservée. Les originaux ne sont jamais remplacés. Les erreurs sont recensées dans le rapport ; on ne doit pas utiliser une sortie en erreur.

Une fois les cinq premiers résultats vérifiés, reprendre dans **le même dossier**, sans `--limit 5`, pour traiter les autres PDF. Les sauvegardes et versions compressées déjà validées sont vérifiées par leur empreinte et réutilisées. Pour un traitement complet, prévoir environ 2 Go libres sur le PC.

```powershell
py -3.12 .\compresser_pdf_local.py --output "$env:USERPROFILE\Downloads\LR-PDF"
```

**Ne rien téléverser, remplacer ni supprimer sur PythonAnywhere à ce stade.** Le « gain potentiel » du rapport ne correspond pas encore à de l'espace libéré sur le serveur. Il faudra vérifier les résultats, le budget de stockage, sauvegarder les fichiers nécessaires et organiser séparément le remplacement. L'outil ne contient aucune fonction d'envoi ou de suppression distante.

Les PDF non liés aux fiches actuelles (notamment certaines versions historiques) ne figurent pas dans le catalogue et ne sont pas récupérés par cet outil. Ne pas les supprimer sur la seule base de ce traitement. La vérification du nombre de pages ne remplace pas le contrôle visuel des petits textes et schémas.

## Vérifications de l'outil

```sh
python -m unittest discover -s outils -p 'test_compresser_pdf_local.py' -v
```

Les tests couvrent les sauvegardes, les URL et noms de fichiers, les erreurs Ghostscript, la conservation des originaux, le nombre de pages, la compression réelle et la reprise. Aucun test ne modifie PythonAnywhere.
