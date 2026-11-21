# Contrôle avant installation

Télécharger `verifier_espace_installation.py` sur le PC. Dans PowerShell :

```powershell
py -3.12 "$env:USERPROFILE\Downloads\verifier_espace_installation.py"
```

Sélectionner `manifest-remplacement.json` **dans le dossier local LR-REMPLACEMENT qui
contient les neuf ZIP**. Le programme vérifie leurs tailles et empreintes, génère
`commande-controle-espace.txt` et copie la commande dans le presse-papiers Windows.
La coller dans Bash PythonAnywhere, puis transmettre le résultat.

Cette commande ne crée, ne retire et ne remplace aucun fichier. Elle vérifie la
présence et le SHA256 des originaux des 736 remplacements. Les absences et modifications
sont signalées, sans restauration. Elle mesure le compte par `du -s -B1`, calcule les
blocs récupérables seulement quand tous les liens d'un inode figurent dans la liste,
et arrondit la taille de chaque contenu compressé au bloc du système de fichiers.
Les contenus de sortie identiques sont comptés une seule fois : cette projection
suppose qu'un installateur préserve effectivement leur partage par liens physiques.

La projection ne comprend pas les journaux, éventuels nouveaux blocs de dossiers,
fichiers de staging ou écritures du site pendant l'opération. La réserve de 8 Mo est
un seuil prudent du contrôleur, pas une exigence de PythonAnywhere ni une garantie de
capacité de transfert. Même une marge suffisante nécessite une organisation par lots.

Ce contrôle n'est pas un installateur. Ne pas supprimer les originaux supplémentaires
ou téléverser les ZIP sur la seule base de cette commande. Un dépassement ou une marge
trop faible doivent être résolus avant les opérations de transfert.
