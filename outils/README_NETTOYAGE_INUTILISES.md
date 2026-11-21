# Nettoyer uniquement les médias sauvegardés et encore sans référence

Télécharger `nettoyer_medias_inutilises.py` sur le PC. L'outil est autonome et nécessite
Python 3.12 standard, Tkinter et PowerShell pour le sélecteur et le presse-papiers.

Simulation, facultative :

```powershell
py -3.12 "$env:USERPROFILE\Downloads\nettoyer_medias_inutilises.py"
```

Préparation du retrait :

```powershell
py -3.12 "$env:USERPROFILE\Downloads\nettoyer_medias_inutilises.py" --appliquer
```

Choisir `rapport-inutilises.json` dans son dossier contenant `originaux` (ne pas choisir
une copie isolée du rapport). Le programme relit intégralement chaque sauvegarde PC et
vérifie taille, SHA256, signature globale d'inventaire, chemins et état de sauvegarde.
Il prépare une commande Bash et la copie dans le presse-papiers, sans accès serveur.
Coller la commande dans Bash PythonAnywhere pour exécuter la simulation ou le nettoyage.

Sur le serveur, la commande :

1. Relit les FileField de tous les modèles et tous les snapshots Version.
2. Vérifie les contenus actuels des seuls chemins sauvegardés ; ignore les fichiers
   absents, modifiés ou redevenus référencés et refuse les symlinks.
3. Relit les références après ces vérifications, avant les retraits.
4. En mode application seulement, revérifie l'identité de chaque fichier puis retire
   le nom correspondant. Les liens physiques non listés gardent leur contenu.

Elle compte les blocs libérés seulement au retrait du dernier lien physique. Elle
n'écrit aucun enregistrement de base, ne crée aucun fichier serveur temporaire et
ne supprime aucun dossier. Les fichiers déjà absents restent absents.

Éviter les imports, restaurations ou modifications de médias pendant ce bref nettoyage :
les vérifications ne verrouillent pas les écritures des autres utilisateurs. Si une
modification est détectée pendant les retraits, les suivants sont interrompus et les
copies PC restent disponibles pour récupération. Conserver intégralement ces copies.

Après application :

```bash
du -sh /home/samr
```

Transmettre les compteurs de nettoyage et l'occupation. Les 736 remplacements ne sont
pas encore installés par cette commande. Le quota peut encore être dépassé après le
seul nettoyage : ne pas téléverser les neuf ZIP simultanément.
