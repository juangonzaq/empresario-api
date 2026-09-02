"""Códigos de referido a 6 dígitos numéricos.

El formato anterior (8 letras/números) era difícil de dictar; el nuevo se
comparte de memoria. Se regeneran TODOS los códigos: un enlace viejo con el
código anterior deja de enlazar (lo ignora ``link_referral``, no rompe el
registro). Los ``Referral`` ya creados no dependen del texto del código.
"""

from __future__ import annotations

import secrets

from django.db import migrations


def _a_seis_digitos(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    usados: set[str] = set()
    for user in User.objects.exclude(referral_code="").only("id", "referral_code"):
        while True:
            code = "".join(secrets.choice("0123456789") for _ in range(6))
            if code not in usados and not User.objects.filter(referral_code=code).exists():
                break
        usados.add(code)
        user.referral_code = code
        user.save(update_fields=["referral_code"])


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0013_alter_onetimetoken_purpose"),
    ]

    operations = [
        migrations.RunPython(_a_seis_digitos, migrations.RunPython.noop),
    ]
