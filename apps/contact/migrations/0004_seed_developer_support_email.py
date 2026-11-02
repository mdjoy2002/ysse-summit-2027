from django.db import migrations


def seed_developer_support_email(apps, schema_editor):
    DeveloperSupportSettings = apps.get_model(
        "contact",
        "DeveloperSupportSettings",
    )
    manager = DeveloperSupportSettings.objects.using(schema_editor.connection.alias)
    contact, _created = manager.get_or_create(key="main")
    if not contact.email:
        contact.email = "mdimtiazsarderjoy@gmail.com"
        contact.save(
            using=schema_editor.connection.alias,
            update_fields=("email", "updated_at"),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("contact", "0003_seed_developer_support_contact"),
    ]

    operations = [
        migrations.RunPython(
            seed_developer_support_email,
            migrations.RunPython.noop,
        ),
    ]
