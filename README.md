# Gestion Stock

## Mise a jour des couts produits depuis Excel

1. Installer la dependance si necessaire :
   ```
   pip install openpyxl
   ```
2. Lancer la commande en visant le fichier Excel, par exemple :
   ```
   python manage.py update_product_costs "C:\Users\PC MARKET\Downloads\Produit (product.template) (1).xlsx"
   ```
3. Le script detecte les colonnes de reference et de cout, mais on peut forcer une colonne en utilisant :
   ```
   --reference-column "Référence interne" --cost-column "Coût"
   ```
4. Utiliser `--match-field sku` si la colonne contient les SKU plutot que manufacturer_reference.

## Import des clients depuis Excel

1. Installer la dependance si necessaire :
   ```
   pip install openpyxl
   ```
2. Importer les clients depuis un fichier Excel :
   ```
   python manage.py import_customers "C:\Users\PC MARKET\Downloads\Contact (res.partner).xlsx"
   ```
3. La commande detecte automatiquement les colonnes (nom, telephone, email, ville, pays, vendeur, activite) et cree les fiches client si le nom est renseigne.

## Dependances

- `openpyxl` (lecture des fichiers `.xlsx`)
