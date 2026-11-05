from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from inventory.models import (
    Brand, Category, Customer, Product, Sale, SaleItem, Site, SiteAssignment,
    MovementType, StockMovement,
)


class Command(BaseCommand):
    help = "Prépare des données fictives, uniquement avec config.demo_settings."

    def handle(self, *args, **options):
        if not getattr(settings, "IS_SALES_DEMO", False):
            raise CommandError("Refus : utilisez --settings=config.demo_settings (base séparée).")
        expected = settings.BASE_DIR / 'var' / 'sales-demo' / 'db.sqlite3'
        if settings.DATABASES['default']['NAME'] != expected:
            raise CommandError("Refus : chemin de la base de démonstration inattendu.")
        call_command('migrate', interactive=False, verbosity=0)
        with transaction.atomic():
            site, _ = Site.objects.get_or_create(name='Treichville — DÉMO')
            user, created = get_user_model().objects.get_or_create(username='demo')
            if created:
                user.set_password('Demo-Ventes-2026!')
                user.is_staff = user.is_superuser = True
                user.save()
            SiteAssignment.objects.get_or_create(user=user, defaults={'site': site})
            brand, _ = Brand.objects.get_or_create(name='Marque démo')
            category, _ = Category.objects.get_or_create(name='Articles démo')
            entry, _ = MovementType.objects.get_or_create(code='DEMO_ENTRY', defaults={
                'name': 'Réception démo', 'direction': MovementType.MovementDirection.ENTRY,
            })
            definitions = [
                ('DS-2CD1043', 'CAMÉRA IP HIKVISION 4 MP', '35000', 8, 'camera'),
                ('RJ45-1029', 'CONNECTEUR RJ45', '50', 124, 'connector'),
                ('CAT6-1M', 'CÂBLE RÉSEAU CAT6 — 1 M', '1000', 3, 'cable'),
            ]
            products = []
            for sku, name, price, quantity, image in definitions:
                product, _ = Product.objects.get_or_create(sku=sku, defaults={
                    'name': name, 'brand': brand, 'category': category,
                    'sale_price': Decimal(price), 'barcode': sku,
                    'manufacturer_reference': sku,
                })
                products.append(product)
                if not product.image:
                    symbols = {
                        'camera': '<rect x="12" y="22" width="44" height="28" rx="8" fill="#c9d2df"/><circle cx="34" cy="36" r="10" fill="#203958"/><circle cx="34" cy="36" r="5" fill="#7694aa"/>',
                        'connector': '<rect x="22" y="12" width="28" height="48" rx="4" fill="#c8d2d9"/><path d="M27 17v18m6-18v18m6-18v18m6-18v18" stroke="#b48b3b" stroke-width="3"/>',
                        'cable': '<ellipse cx="36" cy="36" rx="23" ry="19" fill="none" stroke="#347bb0" stroke-width="9"/><path d="M49 48l13 14" stroke="#347bb0" stroke-width="8"/>',
                    }
                    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="8" fill="#f1f4f8"/>' + symbols[image] + '</svg>'
                    product.image.save(f'demo-{image}.svg', ContentFile(svg.encode()), save=True)
                StockMovement.objects.get_or_create(product=product, document_number='DEMO-INITIAL', site=site, defaults={
                    'movement_type': entry, 'quantity': quantity, 'movement_date': timezone.now(),
                })
            customer, _ = Customer.objects.get_or_create(reference='CLI-DEMO-0042', defaults={
                'name': 'Koné', 'company_name': 'ETS KONÉ DISTRIBUTION', 'phone': '00 00 00 00 00',
                'notes': 'Client fictif de démonstration.',
            })
            # Two genuine outstanding invoices: 60,000 + 22,500 = 82,500.
            for reference, price, paid in [('VTE-DEMO-ANCIEN-1', '70000', '10000'), ('VTE-DEMO-ANCIEN-2', '30000', '7500')]:
                invoice, created = Sale.objects.get_or_create(reference=reference, defaults={
                    'customer': customer, 'customer_name': customer.display_name, 'site': site,
                    'status': Sale.Status.CONFIRMED, 'amount_paid': Decimal(paid),
                })
                if created:
                    SaleItem.objects.create(sale=invoice, product=products[0], quantity=1, unit_price=Decimal(price))
                    invoice._sync_customer_account_entry()
                    invoice._sync_customer_payment_entry()
            quote, created = Sale.objects.get_or_create(reference='DEV-DEMO-EXEMPLE', defaults={
                'customer': customer, 'customer_name': customer.display_name, 'site': site,
                'discount_value': Decimal('10'), 'show_customer_balance': True,
                'previous_customer_balance': customer.unpaid_invoice_balance(),
            })
            if created:
                for product, quantity in zip(products, [1, 10, 2]):
                    SaleItem.objects.create(sale=quote, product=product, quantity=quantity, unit_price=product.sale_price)
        self.stdout.write(self.style.SUCCESS('Démonstration prête : utilisateur demo / mot de passe Demo-Ventes-2026!'))
        self.stdout.write('Base séparée : var/sales-demo/db.sqlite3. Le devis DEV-DEMO-EXEMPLE reproduit la maquette.')
