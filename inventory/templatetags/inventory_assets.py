"""Keep the sales editor's HTML and browser assets on the same revision."""
from hashlib import sha256
from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def versioned_static(name):
    url = static(name)
    source = finders.find(name)
    if source:
        revision = sha256(Path(source).read_bytes()).hexdigest()[:16]
        separator = '&' if '?' in url else '?'
        return f'{url}{separator}v={revision}'
    return url
