"""Recalcula las liquidaciones de cobranza de todas las empresas.

Para después de un deploy que cambie el motor (p. ej. cuando las notas de
crédito empezaron a restar del saldo): los settlements guardados quedan con la
regla vieja hasta que alguien pulse «Conciliar», y este comando los pone al
día sin esperar a cada usuario. Idempotente: los cruces manuales se conservan.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from reconciliation.engine.matching import rebuild_settlements
from reconciliation.models import InvoiceSettlement, ReconciliationRun


class Command(BaseCommand):
    help = "Recalcula las liquidaciones de cobranza (NC incluidas) por empresa."

    def add_arguments(self, parser):
        parser.add_argument("--ruc", help="Solo esta empresa (11 dígitos).")

    def handle(self, *args, **options):
        ruc = options.get("ruc")
        if ruc:
            if not (len(ruc) == 11 and ruc.isdigit()):
                raise CommandError("El RUC debe tener 11 dígitos.")
            rucs = [ruc]
        else:
            # Solo empresas que ya usaron la conciliación: crear settlements
            # de cero para quien nunca concilió no aporta nada (el API de
            # cobranzas recorta por corridas hechas).
            rucs = sorted(
                set(InvoiceSettlement.objects.values_list("account_ruc", flat=True))
                | set(ReconciliationRun.objects.values_list("account_ruc", flat=True))
            )
        if not rucs:
            self.stdout.write("Ninguna empresa ha usado la conciliación todavía.")
            return
        for cuenta in rucs:
            stats = rebuild_settlements(cuenta)
            self.stdout.write(f"{cuenta}: {stats}")
        self.stdout.write(self.style.SUCCESS(f"{len(rucs)} empresa(s) al día."))
