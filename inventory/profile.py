"""Personal profile photo: the authenticated user can edit only their own image."""
from io import BytesIO
import warnings

from PIL import Image, ImageOps
from django import forms
from django.contrib import messages
from django.core.files.base import ContentFile
from django.db import transaction
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .models import UserProfile


class ProfilePhotoForm(forms.Form):
    avatar = forms.ImageField(
        label="Choisir une photo", required=False,
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png,image/webp"}),
        help_text="JPG, PNG ou WebP · 5 Mo maximum.",
    )
    remove_avatar = forms.BooleanField(label="Supprimer ma photo actuelle", required=False)

    def clean_avatar(self):
        uploaded = self.cleaned_data.get("avatar")
        if not uploaded:
            return None
        if uploaded.size > 5 * 1024 * 1024:
            raise forms.ValidationError("La photo doit faire au maximum 5 Mo.")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                uploaded.seek(0)
                with Image.open(uploaded) as source:
                    if source.format not in {"JPEG", "PNG", "WEBP"}:
                        raise forms.ValidationError("Choisissez une image JPG, PNG ou WebP.")
                    if source.width * source.height > 20_000_000:
                        raise forms.ValidationError("La photo ne doit pas dépasser 20 millions de pixels.")
                    photo = ImageOps.exif_transpose(source)
                    photo.thumbnail((512, 512))
                    # Re-encode pixels only: no original filename, EXIF or embedded content.
                    photo = photo.convert("RGB")
                    buffer = BytesIO()
                    photo.save(buffer, format="JPEG", quality=85)
        except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise forms.ValidationError("Cette image est illisible. Choisissez une autre photo.")
        return ContentFile(buffer.getvalue(), name="avatar.jpg")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("avatar") and cleaned.get("remove_avatar"):
            raise forms.ValidationError("Choisissez une nouvelle photo ou sa suppression, pas les deux.")
        return cleaned


@require_http_methods(["GET", "POST"])
def user_profile(request):
    profile = UserProfile.objects.filter(user=request.user).first()
    form = ProfilePhotoForm(request.POST if request.method == "POST" else None, request.FILES if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        photo = form.cleaned_data["avatar"]
        remove = form.cleaned_data["remove_avatar"]
        if photo or remove:
            with transaction.atomic():
                profile, _ = UserProfile.objects.get_or_create(user=request.user)
                previous_name = profile.avatar.name
                storage = profile.avatar.storage
                profile.avatar = photo if photo else ""
                profile.save(update_fields=["avatar"])
                if previous_name and previous_name != profile.avatar.name:
                    transaction.on_commit(lambda: storage.delete(previous_name))
            messages.success(request, "Photo de profil mise à jour.")
        else:
            messages.info(request, "Choisissez une photo à charger.")
        return redirect("inventory:user_profile")
    return render(request, "inventory/user_profile.html", {"form": form, "profile": profile})
