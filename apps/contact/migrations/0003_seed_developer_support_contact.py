from django.db import migrations


DEFAULT_CONTACT = {
    "developer_name": "Md. Imtiaz Sarder",
    "role": "Design & Development",
    "phone": "01736712955",
    "whatsapp_url": "https://wa.me/8801736712955",
    "profile_url": "https://www.facebook.com/mdimtiaz.joy.50",
}


def seed_developer_support_contact(apps, schema_editor):
    DeveloperSupportSettings = apps.get_model(
        "contact",
        "DeveloperSupportSettings",
    )
    manager = DeveloperSupportSettings.objects.using(schema_editor.connection.alias)
    contact, _created = manager.get_or_create(key="main")

    for field, value in DEFAULT_CONTACT.items():
        if not getattr(contact, field):
            setattr(contact, field, value)

    contact.save(
        using=schema_editor.connection.alias,
        update_fields=(*DEFAULT_CONTACT, "updated_at"),
    )


class Migration(migrations.Migration):

    dependencies = [
        ("contact", "0002_developer_support_contact"),
    ]

    operations = [
        migrations.RunPython(
            seed_developer_support_contact,
            migrations.RunPython.noop,
        ),
    ]
