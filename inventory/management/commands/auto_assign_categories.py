from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand

from inventory.category_auto import run_auto_assign_categories


class Command(BaseCommand):
    help = "Auto-assign product categories based on keyword rules."

    def add_arguments(self, parser):
        parser.add_argument(
            "--rules",
            type=str,
            default="category_rules.json",
            help="Path to a JSON rule file. Defaults to category_rules.json in the project root.",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Update categories for all products (default: only uncategorized).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            help="Maximum number of products to evaluate.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without saving.",
        )
        parser.add_argument(
            "--ai",
            action="store_true",
            help="Use Mistral to suggest categories when rules do not match.",
        )

    def handle(self, *args, **options):
        result = run_auto_assign_categories(
            rules_path=Path(options["rules"]).expanduser(),
            apply_all=options["all"],
            limit=options.get("limit"),
            dry_run=options["dry_run"],
            use_ai=options.get("ai"),
        )
        if result.get("empty"):
            self.stdout.write("No uncategorized products found.")
            return
        summary = (
            f"Evaluated {result['evaluated']} products. "
            f"Updated: {result['updated']}, skipped: {result['skipped']}, "
            f"unmatched: {result['unmatched']}."
        )
        self.stdout.write(self.style.SUCCESS(summary))
        change_lines = result.get("change_lines")
        if change_lines is None:
            changes = result.get("changes") or []
            if changes and isinstance(changes[0], dict):
                change_lines = [
                    f"{item.get('sku')} -> {item.get('category')}" for item in changes
                ]
            else:
                change_lines = changes
        if options["dry_run"] and change_lines:
            self.stdout.write("Planned changes:")
            for line in change_lines[:200]:
                self.stdout.write(f"- {line}")
