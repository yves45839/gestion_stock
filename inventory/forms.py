from decimal import Decimal

from django import forms
from django.utils import timezone

from .models import (
    Customer,
    CustomerAccountEntry,
    MovementType,
    Product,
    Sale,
    SaleItem,
    Site,
    StockMovement,
    generate_customer_reference,
)

def _manual_movement_queryset():
    # Allow any movement type to be selected for manual entries to avoid
    # rejecting newly added codes during data setup or tests.
    return MovementType.objects.order_by("name")


class StockMovementForm(forms.ModelForm):
    movement_type = forms.ModelChoiceField(
        queryset=_manual_movement_queryset(),
        label="Type de mouvement",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    movement_date = forms.DateTimeField(
        label="Date du mouvement",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )
    site = forms.ModelChoiceField(
        queryset=Site.objects.order_by("name"),
        label="Site concerné",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    class Meta:
        model = StockMovement
        fields = [
            "product",
            "movement_type",
            "quantity",
            "site",
            "movement_date",
            "document_number",
            "comment",
        ]
        widgets = {
            "product": forms.Select(attrs={"class": "form-control product-select"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1, "step": 1}),
            "site": forms.Select(attrs={"class": "form-control"}),
            "document_number": forms.TextInput(attrs={"class": "form-control"}),
            "comment": forms.Textarea(
                attrs={"rows": 3, "class": "form-control", "placeholder": "Commentaire (optionnel)"}
            ),
        }
        labels = {
            "product": "Produit",
            "quantity": "Quantité",
            "document_number": "Document",
            "comment": "Commentaire",
        }

    def __init__(self, *args, current_site=None, site_locked=False, user=None, **kwargs):
        self._current_site = current_site
        self._site_locked = bool(site_locked and current_site)
        super().__init__(*args, **kwargs)
        if not self.data.get("movement_date") and not self.initial.get("movement_date"):
            self.fields["movement_date"].initial = timezone.now().strftime("%Y-%m-%dT%H:%M")
        if current_site:
            self.fields["site"].initial = current_site
        if self._site_locked:
            self.fields["site"].widget = forms.HiddenInput()
        if user and not getattr(user, "is_superuser", False):
            assignment = getattr(user, "site_assignment", None)
            if assignment:
                self.fields["site"].queryset = Site.objects.filter(pk=assignment.site_id)
            else:
                self.fields["site"].queryset = Site.objects.none()

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity <= 0:
            raise forms.ValidationError("La quantité doit être positive.")
        return quantity

    def clean_site(self):
        site = self.cleaned_data.get("site")
        if self._site_locked and self._current_site and site != self._current_site:
            raise forms.ValidationError("Le site ne peut pas être modifié.")
        if site is None:
            raise forms.ValidationError("Sélectionnez un site.")
        return site


class MovementHeaderForm(forms.Form):
    movement_type = forms.ModelChoiceField(
        queryset=_manual_movement_queryset(),
        label="Type de mouvement",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    movement_date = forms.DateTimeField(
        label="Date du mouvement",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )
    site = forms.ModelChoiceField(
        queryset=Site.objects.order_by("name"),
        label="Site concerné",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    document_number = forms.CharField(
        required=False,
        label="Document",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    comment = forms.CharField(
        required=False,
        label="Commentaire",
        widget=forms.Textarea(
            attrs={"rows": 3, "class": "form-control", "placeholder": "Commentaire (optionnel)"}
        ),
    )

    def __init__(self, *args, current_site=None, site_locked=False, user=None, **kwargs):
        self._current_site = current_site
        self._site_locked = bool(site_locked and current_site)
        self._user = user
        super().__init__(*args, **kwargs)
        if not self.data.get("movement_date") and not self.initial.get("movement_date"):
            self.fields["movement_date"].initial = timezone.now().strftime("%Y-%m-%dT%H:%M")
        if current_site:
            self.fields["site"].initial = current_site
        self._limit_sites()
        if self._site_locked:
            self.fields["site"].widget = forms.HiddenInput()

    def _limit_sites(self):
        if not self._user or getattr(self._user, "is_superuser", False):
            self.fields["site"].queryset = Site.objects.order_by("name")
            return
        assignment = getattr(self._user, "site_assignment", None)
        if assignment:
            self.fields["site"].queryset = Site.objects.filter(pk=assignment.site_id)
            self.fields["site"].initial = assignment.site
        else:
            self.fields["site"].queryset = Site.objects.none()

    def clean_site(self):
        site = self.cleaned_data.get("site")
        if self._site_locked and self._current_site and site != self._current_site:
            raise forms.ValidationError("Le site ne peut pas être modifié.")
        if site is None:
            raise forms.ValidationError("Sélectionnez un site accessible.")
        if site not in self.fields["site"].queryset:
            raise forms.ValidationError("Ce site n'est pas autorisé pour votre compte.")
        return site


class MovementLineForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.order_by("name"),
        label="Produit",
        widget=forms.Select(attrs={"class": "form-control product-select"}),
    )
    quantity = forms.IntegerField(
        min_value=1,
        label="Quantité",
        widget=forms.NumberInput(attrs={"class": "form-control quantity-input", "min": 1, "step": 1}),
    )

    def clean_quantity(self):
        quantity = self.cleaned_data["quantity"]
        if quantity <= 0:
            raise forms.ValidationError("La quantité doit être positive.")
        return quantity


class InventoryAdjustmentForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.all(),
        label="Produit",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    site = forms.ModelChoiceField(
        queryset=Site.objects.order_by("name"),
        label="Site",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    counted_quantity = forms.IntegerField(
        min_value=0,
        label="Stock compte",
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 0}),
    )
    comment = forms.CharField(
        required=False,
        label="Commentaire",
        widget=forms.Textarea(
            attrs={"rows": 2, "class": "form-control", "placeholder": "Precisions sur l'ajustement"}
        ),
    )

    def __init__(self, *args, current_site=None, site_locked=False, **kwargs):
        self._current_site = current_site
        self._site_locked = bool(site_locked and current_site)
        super().__init__(*args, **kwargs)
        if current_site:
            self.fields["site"].initial = current_site
        if self._site_locked:
            self.fields["site"].widget = forms.HiddenInput()

    def clean_counted_quantity(self):
        counted = self.cleaned_data["counted_quantity"]
        if counted < 0:
            raise forms.ValidationError("Le stock compte ne peut pas etre negatif.")
        return counted

    def clean_site(self):
        site = self.cleaned_data.get("site")
        if self._site_locked and self._current_site and site != self._current_site:
            raise forms.ValidationError("Le site ne peut pas être modifié.")
        if site is None:
            raise forms.ValidationError("Sélectionnez un site.")
        return site


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = [
            "sku",
            "manufacturer_reference",
            "name",
            "description",
            "image",
            "is_online",
            "brand",
            "category",
            "barcode",
            "minimum_stock",
            "purchase_price",
            "sale_price",
        ]
        widgets = {
            "sku": forms.TextInput(attrs={"class": "form-control"}),
            "manufacturer_reference": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "description": forms.Textarea(
                attrs={"rows": 3, "class": "form-control", "placeholder": "Description du produit"}
            ),
            "brand": forms.Select(attrs={"class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-control"}),
            "barcode": forms.TextInput(attrs={"class": "form-control"}),
            "minimum_stock": forms.NumberInput(
                attrs={"class": "form-control", "min": 0, "step": 1}
            ),
            "purchase_price": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01"}
            ),
            "sale_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "image": forms.ClearableFileInput(attrs={"class": "form-control-file"}),
        }


class CSVImportForm(forms.Form):
    ENCODING_CHOICES = [
        ("utf-8", "UTF-8"),
        ("latin-1", "Latin-1"),
    ]
    file = forms.FileField(label="Fichier CSV")
    encoding = forms.ChoiceField(choices=ENCODING_CHOICES, initial="utf-8", label="Encodage")
    apply_quantity = forms.BooleanField(
        required=False,
        initial=True,
        label="Creer des entrees de stock a partir de la colonne Qte",
    )
    movement_type = forms.ModelChoiceField(
        queryset=MovementType.objects.filter(direction=MovementType.MovementDirection.ENTRY),
        required=False,
        label="Type de mouvement pour les quantites",
        help_text="Utilise uniquement si la case ci-dessus est cochee.",
    )
    site = forms.ModelChoiceField(
        queryset=Site.objects.order_by("name"),
        required=False,
        label="Site",
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="Choisissez le site auquel rattacher les mouvements importes (le site actif est utilise par defaut).",
    )

    def __init__(self, *args, current_site=None, site_locked=False, **kwargs):
        self._current_site = current_site
        self._site_locked = bool(site_locked and current_site)
        super().__init__(*args, **kwargs)
        if current_site:
            self.fields["site"].initial = current_site
        if self._site_locked:
            self.fields["site"].widget = forms.HiddenInput()

    def clean_site(self):
        site = self.cleaned_data.get("site")
        if self._site_locked:
            if self._current_site and site and site != self._current_site:
                raise forms.ValidationError("Le site ne peut pas etre modifie.")
            return self._current_site
        return site


class ProductAssetBotForm(forms.Form):
    ASSET_CHOICES = (
        ("description", "Descriptions"),
        ("images", "Images"),
        ("techsheet", "Fiche technique"),
        ("pdf", "Brochures PDF"),
        ("videos", "Vidéos"),
        ("blog", "Blog"),
    )
    product = forms.ModelChoiceField(
        queryset=Product.objects.order_by("name"),
        label="Produit ciblé",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    assets = forms.MultipleChoiceField(
        required=False,
        choices=ASSET_CHOICES,
        label="Assets à générer",
        widget=forms.CheckboxSelectMultiple,
    )
    force_description = forms.BooleanField(
        required=False,
        label="Regénérer la description même si elle existe",
    )
    force_image = forms.BooleanField(
        required=False,
        label="Télécharger un nouvel visuel même si un fichier est présent",
    )
    force_techsheet = forms.BooleanField(
        required=False,
        label="Forcer la génération de la fiche technique",
    )
    force_pdf = forms.BooleanField(
        required=False,
        label="Forcer la génération des brochures PDF",
    )
    force_videos = forms.BooleanField(
        required=False,
        label="Forcer la génération des vidéos",
    )
    force_blog = forms.BooleanField(
        required=False,
        label="Forcer la génération du blog",
    )


class ProductAssetBotBulkForm(forms.Form):
    ASSET_CHOICES = ProductAssetBotForm.ASSET_CHOICES
    limit = forms.IntegerField(
        required=False,
        min_value=1,
        label="Nombre maximal de produits",
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "Illimitée si vide"}
        ),
    )
    force_description = forms.BooleanField(
        required=False,
        label="Forcer la génération des descriptions",
    )
    force_image = forms.BooleanField(
        required=False,
        label="Forcer le téléchargement des visuels",
    )
    assets = forms.MultipleChoiceField(
        required=False,
        choices=ASSET_CHOICES,
        label="Assets à générer",
        widget=forms.CheckboxSelectMultiple,
    )
    force_techsheet = forms.BooleanField(
        required=False,
        label="Forcer la génération des fiches techniques",
    )
    force_pdf = forms.BooleanField(
        required=False,
        label="Forcer la génération des brochures PDF",
    )
    force_videos = forms.BooleanField(
        required=False,
        label="Forcer la génération des vidéos",
    )
    force_blog = forms.BooleanField(
        required=False,
        label="Forcer la génération des blogs",
    )


class ProductAssetBotSelectionForm(forms.Form):
    ASSET_CHOICES = ProductAssetBotForm.ASSET_CHOICES
    query = forms.CharField(
        required=False,
        label="Recherche",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Nom, SKU, reference, code-barres"}
        ),
    )
    limit = forms.IntegerField(
        required=False,
        min_value=1,
        label="Limiter l'affichage",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Illimite si vide"}),
    )
    filter_missing_description = forms.BooleanField(
        required=False,
        label="Sans description",
    )
    filter_missing_image = forms.BooleanField(
        required=False,
        label="Sans image",
    )
    force_description = forms.BooleanField(
        required=False,
        label="Forcer la generation des descriptions",
    )
    force_image = forms.BooleanField(
        required=False,
        label="Forcer le telechargement des visuels",
    )
    assets = forms.MultipleChoiceField(
        required=False,
        choices=ASSET_CHOICES,
        label="Assets à générer",
        widget=forms.CheckboxSelectMultiple,
    )
    force_techsheet = forms.BooleanField(
        required=False,
        label="Forcer la generation des fiches techniques",
    )
    force_pdf = forms.BooleanField(
        required=False,
        label="Forcer la generation des brochures PDF",
    )
    force_videos = forms.BooleanField(
        required=False,
        label="Forcer la generation des videos",
    )
    force_blog = forms.BooleanField(
        required=False,
        label="Forcer la generation des blogs",
    )
    products = forms.ModelMultipleChoiceField(
        queryset=Product.objects.none(),
        required=False,
        label="Produits a traiter",
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["products"].queryset = queryset if queryset is not None else Product.objects.none()


class HikvisionDatasheetForm(forms.Form):
    limit = forms.IntegerField(
        required=False,
        min_value=1,
        label="Nombre maximal de produits",
        widget=forms.NumberInput(
            attrs={"class": "form-control", "placeholder": "Illimite si vide"}
        ),
    )
    force = forms.BooleanField(
        required=False,
        label="Forcer le telechargement meme si un PDF est present",
    )
    prefer_lang = forms.ChoiceField(
        label="Langue preferee",
        choices=(
            ("fr", "Francais"),
            ("en", "Anglais"),
            ("any", "Indifferent"),
        ),
        initial="fr",
        widget=forms.Select(attrs={"class": "form-control"}),
    )


class CategoryAutoAssignForm(forms.Form):
    limit = forms.IntegerField(
        required=False,
        min_value=1,
        label="Limiter le traitement",
        widget=forms.NumberInput(attrs={"class": "form-control", "placeholder": "Illimite si vide"}),
    )
    apply_all = forms.BooleanField(
        required=False,
        label="Appliquer a tous les produits",
    )
    dry_run = forms.BooleanField(
        required=False,
        label="Simulation (aucune sauvegarde)",
    )
    rules_path = forms.CharField(
        required=False,
        label="Fichier de regles",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "category_rules.json"}
        ),
    )
    use_ai = forms.BooleanField(
        required=False,
        initial=True,
        label="Utiliser Mistral pour les correspondances manquantes",
    )


class SaleForm(forms.ModelForm):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.none(),
        required=False,
        label="Client existant",
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="Sélectionnez un client enregistré. Saisissez un nom ci-dessous pour créer un nouveau client.",
    )
    sale_date = forms.DateTimeField(
        label="Date de vente",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )
    amount_paid = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        required=False,
        min_value=0,
        label="Montant payé",
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
    )

    class Meta:
        model = Sale
        fields = ["reference", "sale_date", "customer", "customer_name", "amount_paid", "notes"]
        widgets = {
            "reference": forms.TextInput(attrs={"class": "form-control"}),
            "customer_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Nom du client si non enregistré",
                }
            ),
            "notes": forms.Textarea(
                attrs={"class": "form-control", "rows": 3, "placeholder": "Notes ou conditions"}
            ),
        }
        labels = {
            "reference": "Reference",
            "customer_name": "Client",
            "notes": "Notes",
            "amount_paid": "Montant payé",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.objects.order_by("name", "company_name")
        self.fields["customer"].empty_label = "— Aucun client —"
        if not self.data.get("sale_date") and not self.initial.get("sale_date"):
            self.fields["sale_date"].initial = timezone.now().strftime("%Y-%m-%dT%H:%M")

    def clean_amount_paid(self):
        amount = self.cleaned_data.get("amount_paid")
        if amount is None:
            return Decimal("0.00")
        return amount

    def clean(self):
        cleaned = super().clean()
        customer = cleaned.get("customer")
        customer_name = (cleaned.get("customer_name") or "").strip()
        cleaned["customer_name"] = customer_name
        if not customer and not customer_name:
            raise forms.ValidationError("Sélectionnez un client ou indiquez un nom pour en créer un.")
        if customer and not customer_name:
            cleaned["customer_name"] = customer.display_name
        return cleaned


class SaleItemForm(forms.Form):
    line_type = forms.ChoiceField(
        choices=SaleItem.LineType.choices,
        initial=SaleItem.LineType.PRODUCT,
        widget=forms.HiddenInput(attrs={"class": "line-type-input"}),
    )
    product = forms.ModelChoiceField(
        queryset=Product.objects.all(),
        label="Produit",
        required=False,
        widget=forms.Select(attrs={"class": "form-control product-select"}),
    )
    quantity = forms.IntegerField(
        min_value=0,
        initial=1,
        label="Quantite",
        widget=forms.NumberInput(attrs={"class": "form-control quantity-input", "min": 0}),
    )
    unit_price = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        required=False,
        label="Prix unitaire",
        widget=forms.NumberInput(
            attrs={"class": "form-control unit-price-input", "step": "0.01", "min": 0}
        ),
    )
    scan_code = forms.CharField(
        required=False,
        label="Code QR / Scan",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Scannez ou saisissez un code"}
        ),
    )
    description = forms.CharField(
        required=False,
        label="Description",
        widget=forms.TextInput(
            attrs={"class": "form-control description-input", "placeholder": "Description"}
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].required = False

    def clean(self):
        cleaned_data = super().clean()
        line_type = cleaned_data.get("line_type") or SaleItem.LineType.PRODUCT
        cleaned_data["line_type"] = line_type
        scan_code = (cleaned_data.get("scan_code") or "").strip()
        cleaned_data["scan_code"] = scan_code
        if line_type == SaleItem.LineType.PRODUCT:
            product = cleaned_data.get("product")
            if not product and scan_code:
                product_match = Product.objects.for_scan_code(scan_code).first()
                if not product_match:
                    raise forms.ValidationError(f"Aucun produit ne correspond au code {scan_code}.")
                cleaned_data["product"] = product_match
                self.cleaned_data["product"] = product_match
            if not cleaned_data.get("product"):
                raise forms.ValidationError("Choisissez un produit ou scannez un code QR valide.")
            quantity = cleaned_data.get("quantity")
            if quantity is None or quantity <= 0:
                raise forms.ValidationError("Indiquez une quantite positive.")
        else:
            description = (cleaned_data.get("description") or "").strip()
            if not description:
                raise forms.ValidationError("Une description est requise pour cette ligne.")
            cleaned_data["description"] = description
            cleaned_data["product"] = None
            cleaned_data["scan_code"] = ""
            cleaned_data["quantity"] = 0
            cleaned_data["unit_price"] = Decimal("0.00")
        return cleaned_data


class SaleReturnItemForm(forms.Form):
    sale_item_id = forms.IntegerField(widget=forms.HiddenInput())
    return_quantity = forms.IntegerField(
        min_value=0,
        label="Quantité à retourner",
        widget=forms.NumberInput(
            attrs={"class": "form-control", "type": "number", "min": 0, "step": 1}
        ),
    )

    def clean_return_quantity(self):
        value = self.cleaned_data.get("return_quantity")
        return value if value is not None else 0


class SaleAdjustmentItemForm(forms.Form):
    sale_item_id = forms.IntegerField(widget=forms.HiddenInput())
    keep_quantity = forms.IntegerField(
        min_value=0,
        label="Quantit� � conserver",
        widget=forms.NumberInput(
            attrs={"class": "form-control", "type": "number", "min": 0, "step": 1}
        ),
    )
    unit_price = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        label="Prix unitaire",
        widget=forms.NumberInput(
            attrs={"class": "form-control", "step": "0.01", "min": 0}
        ),
    )

    def clean_keep_quantity(self):
        value = self.cleaned_data.get("keep_quantity")
        return value if value is not None else 0

class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = [
            "reference",
            "name",
            "company_name",
            "email",
            "phone",
            "address",
            "credit_limit",
            "notes",
        ]
        widgets = {
            "reference": forms.TextInput(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "company_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "credit_limit": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }
        labels = {
            "reference": "Référence",
            "name": "Nom du contact",
            "company_name": "Société",
            "email": "Email",
            "phone": "Téléphone",
            "address": "Adresse",
            "credit_limit": "Plafond de crédit",
            "notes": "Notes",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk and not self.data.get("reference") and not self.initial.get("reference"):
            self.fields["reference"].initial = generate_customer_reference()

    def clean_credit_limit(self):
        credit_limit = self.cleaned_data.get("credit_limit") or Decimal("0.00")
        if credit_limit < 0:
            raise forms.ValidationError("Le plafond ne peut pas être négatif.")
        return credit_limit

    def clean_reference(self):
        reference = (self.cleaned_data.get("reference") or "").strip()
        if reference:
            return reference
        return generate_customer_reference()


class CustomerAccountEntryForm(forms.ModelForm):
    occurred_at = forms.DateTimeField(
        label="Date",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}),
    )

    class Meta:
        model = CustomerAccountEntry
        fields = ["entry_type", "label", "amount", "occurred_at", "notes"]
        widgets = {
            "entry_type": forms.Select(attrs={"class": "form-control"}),
            "label": forms.TextInput(attrs={"class": "form-control"}),
            "amount": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": 0}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }
        labels = {
            "entry_type": "Type",
            "label": "Libellé",
            "amount": "Montant",
            "notes": "Notes",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.data.get("occurred_at") and not self.initial.get("occurred_at"):
            self.fields["occurred_at"].initial = timezone.now().strftime("%Y-%m-%dT%H:%M")

    def clean_amount(self):
        amount = self.cleaned_data.get("amount") or Decimal("0.00")
        if amount <= 0:
            raise forms.ValidationError("Indiquez un montant positif.")
        return amount


class BaseSaleItemFormSet(forms.BaseFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        product_lines = 0
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            if form.cleaned_data.get("line_type") == SaleItem.LineType.PRODUCT:
                product_lines += 1
        if product_lines == 0:
            raise forms.ValidationError("Ajoutez au moins un produit a la vente.")


SaleItemFormSet = forms.formset_factory(
    SaleItemForm,
    formset=BaseSaleItemFormSet,
    extra=0,
    can_delete=True,
    min_num=1,
    validate_min=True,
)
