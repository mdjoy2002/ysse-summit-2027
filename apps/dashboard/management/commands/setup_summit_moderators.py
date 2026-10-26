"""Create the baseline least-privilege group for summit moderators."""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from apps.dashboard.access import MODERATOR_GROUP_NAME


MODERATOR_PERMISSIONS = (
    ("registrations", "view_registration"),
    ("registrations", "view_passtype"),
    ("registrations", "manage_registration_status"),
    ("competitions", "view_competition"),
    ("competitions", "view_competitionsubmission"),
    ("competitions", "review_competitionsubmission"),
    ("home", "view_featuredspeaker"),
    ("speakers", "view_speakercategory"),
    ("schedule", "view_schedulesession"),
    ("partners", "view_sponsor"),
    ("partners", "view_partnershipinquiry"),
    ("contact", "view_contactinquiry"),
    ("payments", "view_paymenttransaction"),
)


class Command(BaseCommand):
    """Create the moderator group and add safe baseline model permissions."""

    help = "Create or update the YSSE Summit Moderators group."

    def handle(self, *args, **options):
        group, created = Group.objects.get_or_create(
            name=MODERATOR_GROUP_NAME
        )
        permissions = []
        missing = []
        for app_label, codename in MODERATOR_PERMISSIONS:
            permission = Permission.objects.filter(
                content_type__app_label=app_label,
                codename=codename,
            ).first()
            if permission is None:
                missing.append(f"{app_label}.{codename}")
            else:
                permissions.append(permission)
        if missing:
            raise self.CommandError(
                "Run migrations before setting up moderators; missing permissions: "
                + ", ".join(missing)
            )
        group.permissions.add(*permissions)
        action = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {MODERATOR_GROUP_NAME} with "
                f"{len(permissions)} baseline permissions."
            )
        )
        self.stdout.write(
            "In Django Admin, mark moderator accounts as staff and add them "
            "to this group. Assign subscriber, payment reconciliation, or "
            "other sensitive permissions separately when required."
        )
