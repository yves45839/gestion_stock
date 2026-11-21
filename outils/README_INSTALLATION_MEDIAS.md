# Installer les neuf lots avec un compte initialement au-dessus du quota

Cette procédure concerne les 736 chemins du manifeste v2, dont les versions compressées
sont strictement inférieures à 1 Mio. Les anciens fichiers supprimés manuellement, les
six gros fichiers exclus et les médias sans référence nettoyés ne figurent pas dans
ces lots et ne sont pas réintroduits. Les autres fichiers du compte restent en place.

## Préconditions et maintenance

Conserver les originaux dans `Downloads/LR-PDF/originaux` et `Downloads/LR-IMAGES/originaux`,
ainsi que les neuf ZIP dans le dossier contenant `manifest-remplacement.json`.
L'outil PC vérifie chaque original par taille/SHA256, chaque ZIP et chaque contenu
compressé avant de préparer les commandes. Il ne se connecte pas au serveur.

**Les images et PDF concernés seront temporairement indisponibles entre libération
et installation.** Choisir un moment calme ; éviter imports, restaurations, modifications
ou suppressions de médias pendant l'opération. Les commandes ne modifient aucun code
d'application ni enregistrement de base. Elles ne bloquent pas les écritures des autres
utilisateurs : la fenêtre de maintenance est donc nécessaire.

## 1. Préparer et libérer

Télécharger `installer_lots_medias.py` sur le PC puis, dans PowerShell Windows :

```powershell
py -3.12 "$env:USERPROFILE\Downloads\installer_lots_medias.py" --copier liberer
```

Choisir le manifeste dans son dossier de ZIP. Coller la commande copiée dans Bash
PythonAnywhere. Ne pas fermer la console pendant son exécution.

Avant tout retrait, la commande vérifie que chaque original est encore présent et
correspond au manifeste. Une nouvelle absence ou modification arrête la phase sans
nouveau retrait. Elle calcule les blocs effectivement libérables, l'espace final et
le pic estimé, y compris une éventuelle double copie pendant les téléversements
séquentiels. Les unités d'allocation sont estimées d'après les fichiers existants,
sans arrondir arbitrairement chaque contenu à 1 Mio.

Le contrôleur impose une réserve de 8 Mio à la fin et de 2 Mio au pic des transferts ;
ce sont des marges de l'outil, pas des règles de PythonAnywhere. Une projection
insuffisante arrête la commande avant les retraits. Un contrôle sans retrait peut
aussi être préparé en remplaçant `--copier liberer` par `--copier controle`.

La commande retire uniquement les originaux préalablement vérifiés et sauvegardés.
Elle laisse leurs répertoires en place et conserve tout lien physique hors du plan.
Au-dessus du quota, le journal ne peut pas être écrit immédiatement : une phase
d'amorçage retire les premiers originaux jusqu'à disposer de place. Chaque retrait
d'amorçage est annoncé dans la console. Dès que possible, le journal est enregistré
dans `/home/samr/.lr-remplacement/<identifiant>/etat.json` ; les retraits suivants
utilisent des intentions persistantes et des écritures atomiques avec fsync.

**En cas d'interruption avant la création du journal**, les originaux déjà retirés
restent récupérables depuis le PC, mais la reprise automatique est impossible : conserver
la sortie console et demander une récupération accompagnée. Ne pas relancer aveuglément.
Après création du journal, une libération interrompue se reprend explicitement avec
`--copier reprendre`, après examen de l'erreur. Le contrôle revérifie les fichiers
restants et n'accepte comme absences que les retraits inscrits au journal.

Attendre **« Libération terminée »** avant le transfert.

## 2. Envoyer les ZIP

Dans PythonAnywhere → Files, ouvrir **`/home/samr/`**. Téléverser les fichiers
**`lot-001.zip` à `lot-009.zip`, un par un et dans cet ordre**. Attendre la fin de
chaque téléversement avant le suivant. Ne pas les extraire manuellement.

Les copies locales doivent rester présentes. Le transfert simultané de tous les ZIP
ne respecte pas le calcul de pic utilisé par le contrôleur.

## 3. Installer

Sur le PC :

```powershell
py -3.12 "$env:USERPROFILE\Downloads\installer_lots_medias.py" --copier installer
```

Choisir le même manifeste puis coller la commande dans Bash PythonAnywhere.

La commande exige un journal de libération complet correspondant au plan. Elle vérifie
tous les ZIP nécessaires et les destinations avant de consommer le premier lot. Une
destination réapparue avec un autre contenu interrompt l'installation sans l'écraser.

Chaque ZIP est revalidé, chargé en mémoire (environ 20 Mio maximum par ZIP), puis sa
copie **serveur** est retirée avant de créer les fichiers. Les copies **PC** restent
intactes. Les contenus de moins de 1 Mio sont écrits dans un nouveau fichier puis liés
aux noms prévus sans écrasement, avec partage des contenus identiques entre lots.
Aucun inode d'origine partagé n'est tronqué ou réécrit. Les groupes installés sont
suivis dans le journal.

Après interruption d'une installation, retéléverser le lot demandé si sa copie serveur
a déjà été consommée, puis relancer l'installation avec le même manifeste. Les groupes
partiels sont reconnus par SHA256 et les liens sont complétés. Un fichier supprimé
après une installation déjà journalisée n'est pas restauré automatiquement ; il est
signalé lors de la vérification finale.

Transmettre les résultats **« Vérification finale »**, **« Occupation actuelle »** et
toute erreur. Conserver les sauvegardes PC et le journal après la réussite. Le journal
et les dossiers réels ajoutent une petite quantité au résultat de la projection.
