from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("contact", "0004_seed_developer_support_email"),
    ]

    operations = [
        migrations.DeleteModel(
            name="DeveloperSupportSettings",
        ),
    ]
