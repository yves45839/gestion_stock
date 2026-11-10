import re
from django import forms


def normalize_phone(value):
    value = re.sub(r"[\s().-]", "", value or "")
    if value.startswith("00"):
        value = "+" + value[2:]
    if not re.fullmatch(r"\+[1-9]\d{7,14}", value):
        raise forms.ValidationError("Indiquez le numéro international, par exemple +225 suivi du numéro complet.")
    return value


class DeliveryForm(forms.Form):
    channels = forms.MultipleChoiceField(
        label="Envoyer par", choices=[("email", "Email"), ("whatsapp", "WhatsApp")],
        widget=forms.CheckboxSelectMultiple,
    )
    email = forms.EmailField(label="Email du destinataire", required=False)
    phone = forms.CharField(label="WhatsApp du destinataire", required=False, max_length=40)
    file_format = forms.ChoiceField(label="Format", choices=[("pdf", "PDF"), ("png", "Images PNG (une par page)")])
    subject = forms.CharField(label="Objet de l’email", max_length=150)
    message = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 4}), max_length=900)
    whatsapp_consent = forms.BooleanField(label="Le destinataire accepte de recevoir ce document sur WhatsApp", required=False)

    def __init__(self, *args, report=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not report:
            self.fields["file_format"].choices = [("pdf", "PDF")]

    def clean(self):
        data = super().clean()
        channels = data.get("channels", [])
        if "email" in channels and not data.get("email"):
            self.add_error("email", "Indiquez le destinataire de l’email.")
        if "whatsapp" in channels:
            try:
                data["phone"] = normalize_phone(data.get("phone"))
            except forms.ValidationError as error:
                self.add_error("phone", error)
            if not data.get("whatsapp_consent"):
                self.add_error("whatsapp_consent", "Confirmez l’accord du destinataire.")
        if "\n" in data.get("subject", "") or "\r" in data.get("subject", ""):
            self.add_error("subject", "L’objet doit tenir sur une ligne.")
        return data
