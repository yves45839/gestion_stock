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

Le script transfère seulement les lignes `selectionne` avec une version compressée
**strictement inférieure à 1 048 576 octets**. Les fichiers signalés supprimés ou modifiés
restent exclus. **Les originaux serveur des versions compressées de 1 Mo ou plus doivent
être retirés, et non conservés sur le serveur.** Ils sont inscrits dans `removals` après
vérification des deux copies locales (original et version compressée).
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

Pour les fichiers dont la compression n'avait apporté aucun gain, le script relit
également les anciens rapports `LR-PDF/rapport.json` et `LR-IMAGES/rapport-images.json`.
Les copies originales vérifiées de moins de 1 Mo sont à laisser en place si présentes
(aucun transfert nécessaire). Les copies de 1 Mo ou plus figurent aussi dans `removals`,
avec `server_existence_unverified: true` : l'ancien audit ne vérifiait pas leur présence.
Les erreurs sans sauvegarde vérifiée, notamment `products/images/F40.pdf`, ne sont
jamais supprimées. Aucun fichier hors des rapports n'est retiré automatiquement.

Deux commandes Bash sont préparées dans des fichiers texte :

- `commande-retraits-simulation.txt` : vérifie la présence et le SHA256 actuel de tous
  les originaux à retirer et calcule les blocs récupérables en tenant compte des liens
  physiques. Aucun changement serveur.
- `commande-retraits-appliquer.txt` : refait les vérifications puis retire uniquement
  ces noms. Un fichier déjà absent reste absent. Un contenu modifié arrête la phase
  avant tout retrait ; une modification pendant la phase interrompt les retraits suivants.
  Les autres noms partageant le même contenu ne sont pas modifiés.

Sur PowerShell, pour copier la commande de simulation (remplacer le dossier) :

```powershell
Get-Content "C:\chemin\LR-REMPLACEMENT-date-identifiant\commande-retraits-simulation.txt" -Raw -Encoding UTF8 | Set-Clipboard
```

Coller ensuite dans Bash PythonAnywhere. Il s'agit d'un `printf` envoyant le code en
mémoire à Python, sans fichier temporaire ni écriture `.pyc` sur le serveur plein.
Conserver les deux copies locales après toute application. Les fichiers retirés ne
seront plus accessibles par leurs anciens liens sur le site ; la base n'est pas modifiée.

**Ce programme prépare seulement les lots locaux.** Il ne se connecte pas au serveur,
n'effectue aucun transfert et n'installe rien. Les commandes préparées ne sont exécutées
que lorsque l'utilisateur les colle dans sa console. Ne pas extraire ces archives dans
`media`. Un installateur devra vérifier à nouveau l'existence et le SHA256 de chaque
destination, ignorer toute suppression ou modification postérieure à la sélection,
remplacer atomiquement et conserver le partage des contenus identiques.

Un seuil par fichier ne contourne pas le quota total du compte. L'ancienne estimation
de 544 Mo conservait les originaux exclus et n'est plus la règle choisie. Le retrait
des gros fichiers et le partage des contenus peuvent rapprocher le résultat de 512 Mo,
mais les tailles exactes, les autres liens physiques et la capacité de staging doivent
être vérifiés sur le serveur. La simulation seule ne libère rien. Même après les seuls
retraits, le compte peut rester au-dessus du quota : ne pas supposer le transfert possible.
