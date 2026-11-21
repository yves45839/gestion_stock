# Sauvegarder les médias sans référence actuelle ou historique

L'outil `sauvegarder_medias_inutilises.py` ne supprime et ne modifie aucun fichier serveur.
Il prépare une commande de lecture de l'application et sauvegarde les contenus sur le PC.
Il fonctionne avec Python 3.12 standard et PowerShell Windows.

1. Télécharger le script sur le PC, puis lancer :

   ```powershell
   py -3.12 "$env:USERPROFILE\Downloads\sauvegarder_medias_inutilises.py"
   ```

2. La commande d'inventaire est copiée dans le presse-papiers. La coller dans Bash
   PythonAnywhere. Elle parcourt tous les FileField via les gestionnaires de base, ainsi
   que tous les snapshots Version ; aucun fichier référencé par l'un ou l'autre n'est
   inventorié. Les symlinks et contenus qui changent pendant la lecture sont refusés.
   Le résultat complet est encadré par `LR_INUTILISES_DEBUT` et `LR_INUTILISES_FIN`.

3. Copier ce bloc complet, marqueurs compris, depuis la console serveur, puis lancer
   sur le PC :

   ```powershell
   py -3.12 "$env:USERPROFILE\Downloads\sauvegarder_medias_inutilises.py" --sauvegarder
   ```

   Le script lit le presse-papiers. Une sortie enregistrée dans un fichier texte peut
   aussi être fournie avec `--inventaire "C:\chemin\inventaire.txt"`.

4. Transmettre le `rapport-inutilises.json` dont le chemin est affiché à la fin.

Les copies restent dans `Downloads/LR-INUTILISES/<identifiant-inventaire>/originaux`.
Les téléchargements utilisent seulement GET HTTPS vers le site, avec redirections
hors du site refusées. Chaque sauvegarde est comparée à la taille et au SHA256 lus
sur le serveur. Une copie existante différente n'est jamais écrasée. Les fichiers
temporaires sont retirés en cas d'erreur. Relancer avec le même inventaire reprend
les sauvegardes déjà vérifiées. Les 404/410 sont marqués déjà absents, les autres
erreurs ne sont pas assimilées à des suppressions.

Un inventaire et une sauvegarde ne prouvent pas qu'un fichier reste inutilisé plus tard.
Un futur nettoyage doit vérifier de nouveau les références actuelles et historiques,
le contenu et la présence, et ne retirer que les chemins sauvegardés et vérifiés.
Cette sauvegarde n'autorise pas de suppression générale du dossier media.
