# Préparer les lots de remplacement sur le PC

`preparer_lots_remplacement.py` fonctionne avec Python 3.12 sans bibliothèque externe.
Il utilise `selection-remplacement.json` et les dossiers locaux `Downloads/LR-PDF` et
`Downloads/LR-IMAGES`. Le rapport de sélection doit être complet et concerner
`https://samr.pythonanywhere.com`.

Sur Windows :

```powershell
py -3.12 "$env:USERPROFILE\Downloads\preparer_lots_remplacement.py"
```

Une fenêtre demande de sélectionner le rapport JSON. Si le dossier Téléchargements
est déplacé, utiliser `--downloads "D:\chemin\Téléchargements"`.

Le script conserve seulement les lignes `selectionne` avec une version compressée
**strictement inférieure à 1 048 576 octets**. Les fichiers signalés supprimés, modifiés
ou non sélectionnés, et les versions compressées de 1 Mo ou plus, restent exclus.
Les originaux et les sorties sont vérifiés localement par taille et SHA256 avant
de créer un nouveau dossier `LR-REMPLACEMENT-date-identifiant`.

Les lots ZIP contiennent des blocs de contenu, et non des chemins directement
extractibles dans `media`. Les originaux identiques utilisent la plus petite de leurs
versions compressées vérifiées afin de conserver le partage de contenu. Le manifeste
conserve tous les noms de destination et les empreintes attendues avant remplacement.
Les lots ont environ 20 Mo de contenu au maximum (hors en-têtes ZIP).

Le dossier comprend `manifest-remplacement.json`, à transmettre pour la suite. Un
échec pendant l'écriture laisse un manifeste `complete: false`, inutilisable pour une
installation. Les anciens dossiers, rapports et sauvegardes restent intacts.

**Ce programme prépare seulement les lots locaux.** Il ne se connecte pas au serveur,
n'effectue aucun transfert et n'installe rien. Ne pas extraire ces archives dans
`media`. Un installateur devra vérifier à nouveau l'existence et le SHA256 de chaque
destination, ignorer toute suppression ou modification postérieure à la sélection,
remplacer atomiquement et conserver le partage des contenus identiques.

Un seuil par fichier ne contourne pas le quota total du compte. À 676 Mo pour un quota
de 512 Mo, le transfert reste bloqué. La sélection du 20 novembre avec ce seuil aurait
encore laissé environ 544 Mo après remplacement ; il faut de la capacité supplémentaire
ou une réduction du stockage avant de pouvoir terminer l'installation et permettre au
site de fonctionner normalement.
