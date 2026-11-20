# Sauvegarder et optimiser les images sur le PC

Lancer cet outil **sur votre ordinateur Windows**, pas sur PythonAnywhere. Il ne contient aucune fonction d'envoi, remplacement ou suppression distante.

## Préparer

Python 3.12 est déjà utilisé pour les PDF. Installer Pillow sur le PC :

```powershell
py -3.12 -m pip install Pillow
```

Télécharger `optimiser_images_local.py` depuis cette branche, puis l'exécuter :

```powershell
py -3.12 .\optimiser_images_local.py --output "$env:USERPROFILE\Downloads\LR-IMAGES"
```

Ou utiliser une fenêtre de sélection pour éviter les erreurs de chemin :

```powershell
& {
    Add-Type -AssemblyName System.Windows.Forms
    $choix = New-Object System.Windows.Forms.OpenFileDialog
    $choix.Title = "Choisir optimiser_images_local.py"
    $choix.Filter = "Fichiers Python|*.py"
    if ($choix.ShowDialog() -eq "OK") {
        py -3.12 $choix.FileName --output "$env:USERPROFILE\Downloads\LR-IMAGES"
    }
}
```

L'outil lit le catalogue public existant puis récupère les images actuelles et les aperçus en attente. Il ne récupère pas les anciennes images conservées uniquement dans l'historique ou les fichiers orphelins.

## Résultats

Dans `Téléchargements\LR-IMAGES` :

- `originaux` : copies intactes, avec empreintes SHA256 consignées dans le rapport.
- `optimisees` : candidats moins volumineux ; les chemins, noms et formats sont conservés.
- `rapport-images.csv` et `rapport-images.json` : tailles, gains, dimensions, empreintes et états.

Réglage proposé : côté le plus long limité à **1 600 pixels**, sans agrandissement ni recadrage. JPEG/WebP : qualité 85. PNG : compression sans perte supplémentaire après le redimensionnement ; transparence conservée. L'orientation EXIF est appliquée aux pixels, mais les autres métadonnées EXIF ne sont pas reprises. Les profils ICC sont conservés. Les SVG, images animées et formats non pris en charge sont sauvegardés sans optimisation.

Le réglage diminue la résolution des grandes images et peut perdre des détails : comparer quelques photos et images comportant du texte avec leurs originaux avant de valider un remplacement. Les originaux sur le PC restent conservés et aucun changement n'est appliqué au site.

Les fichiers déjà traités sont vérifiés par leur empreinte lors d'une reprise dans le même dossier. Ne pas modifier à la main les dossiers de sauvegarde ou de candidats. Un réglage différent nécessite un nouveau dossier de résultats.

Transmettre les volumes, le gain potentiel et les erreurs, puis au besoin le fichier `rapport-images.json`, pour calculer le budget global. Les gains affichés ne sont pas encore libérés sur le serveur et devront être combinés avec les PDF sans compter deux fois d'éventuels doublons.

## Tests

```sh
python -m unittest discover -s outils -p 'test_optimiser_images_local.py' -v
```

Pillow est nécessaire pour ces tests. Aucun test ne modifie PythonAnywhere.
