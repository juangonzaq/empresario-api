from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0010_businessprofile_sectors_goals"),
    ]

    operations = [
        migrations.AddField(
            model_name="businessprofile", name="has_payroll",
            field=models.BooleanField(blank=True, default=None, null=True,
                                      verbose_name="tiene trabajadores en planilla"),
        ),
    ]
