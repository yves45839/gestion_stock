"""Processeurs de contexte pour la barre de navigation."""

from .models import Site


def site_switcher(request):
    """Expose la liste des sites au sélecteur de site du topbar.

    Réservé aux superutilisateurs : les autres comptes restent liés à leur
    site assigné (SiteAssignment) et voient une simple pastille.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not user.is_superuser:
        return {}
    return {
        "nav_sites": Site.objects.order_by("name"),
        "nav_active_site_id": str(request.session.get("active_site_id") or ""),
    }
