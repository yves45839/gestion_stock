# Actualiser la sélection après des suppressions sur PythonAnywhere

Télécharger `actualiser_selection_locale.py` sur le PC (bouton **Download raw file** de GitHub).
Ce script est autonome, utilise Python 3.12 et ne nécessite aucune installation de bibliothèque.

Dans PowerShell Windows, ouvrir un sélecteur puis choisir ce script :

```powershell
& {
    Add-Type -AssemblyName System.Windows.Forms
    $choix = New-Object System.Windows.Forms.OpenFileDialog
    $choix.Title = "Choisir actualiser_selection_locale.py"
    $choix.Filter = "Fichiers Python|*.py"
    if ($choix.ShowDialog() -eq "OK") {
        py -3.12 $choix.FileName
    }
}
```

Les dossiers `Downloads\LR-PDF` et `Downloads\LR-IMAGES` doivent contenir leurs anciens
rapports JSON et leurs originaux et versions compressées. Avec Téléchargements déplacé,
utiliser `--downloads "D:\chemin\Téléchargements"`.

Le script relit le catalogue public actuel, vérifie les sauvegardes locales par SHA256 et
lit les fichiers candidats du serveur pour comparer leur contenu. Les 404/410, les
chemins retirés du catalogue et les contenus modifiés sont exclus. Les nouveaux chemins
sont signalés séparément, sans remplacer les sauvegardes existantes. Les autres erreurs
HTTP sont des erreurs, pas une preuve de suppression.

Il crée uniquement un nouveau dossier local `LR-SELECTION-date-heure` avec
`selection-remplacement.json`. Les anciens rapports et fichiers restent intacts.
Une exécution interrompue laisse `complete: false` : ne pas utiliser ce rapport pour un remplacement.

Transmettre le rapport pour préparer le remplacement. **Ce script ne remplace rien sur
PythonAnywhere**, ne supprime aucun fichier et ne modifie pas la base. Sa vérification est
un instantané : un futur outil de remplacement doit revérifier les contenus sur le
serveur juste avant chaque changement. La somme des gains par chemin ne représente pas
les blocs récupérables si des liens physiques sont présents.

Le dépassement du quota empêche encore les téléversements ; cette sélection ne résout
pas à elle seule la capacité nécessaire au transfert.
