"""Chromium PDF fallback for the isolated demo when native WeasyPrint is absent."""
import os
from pathlib import Path
import subprocess
import sys


class DemoPDFError(Exception):
    pass


def should_use_demo_pdf(native_html):
    from django.conf import settings
    return getattr(settings, "IS_SALES_DEMO", False) and (
        native_html is None or os.environ.get("LRSTOCK_DEMO_PDF_ENGINE") == "chromium"
    )


def render_demo_pdf(html):
    from django.conf import settings
    if not getattr(settings, "IS_SALES_DEMO", False):
        raise DemoPDFError("Le moteur PDF de secours est réservé à la démonstration isolée.")
    try:
        process = subprocess.run(
            [sys.executable, "-m", "inventory.demo_pdf"], input=html.encode("utf-8"),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
            cwd=Path(__file__).resolve().parent.parent,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise DemoPDFError("La génération PDF n’a pas abouti. Arrêtez la démonstration et relancez start_demo.py.")
    if process.returncode or not process.stdout.startswith(b"%PDF-"):
        raise DemoPDFError("Le moteur PDF de démonstration n’est pas prêt. Fermez le serveur et relancez : py -3.12 start_demo.py")
    return process.stdout


def _worker():
    from playwright.sync_api import sync_playwright
    html = sys.stdin.buffer.read().decode("utf-8")
    with sync_playwright() as playwright:
        options = {"headless": True}
        # Explicit local override is useful on managed Linux test executors.
        executable = os.environ.get("LRSTOCK_DEMO_CHROMIUM_EXECUTABLE")
        if executable:
            options["executable_path"] = executable
        browser = playwright.chromium.launch(**options)
        try:
            context = browser.new_context(java_script_enabled=False, service_workers="block")
            # The templates are inline documents: do not fetch network/file resources.
            context.route("**/*", lambda route: route.abort())
            page = context.new_page()
            page.set_content(html, wait_until="load", timeout=15000)
            content = page.pdf(format="A4", print_background=True, prefer_css_page_size=True)
            sys.stdout.buffer.write(content)
        finally:
            browser.close()


if __name__ == "__main__":
    _worker()
