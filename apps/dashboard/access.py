"""Shared server-side access rules for the moderator dashboard."""

MODERATOR_GROUP_NAME = "YSSE Summit Moderators"


def has_dashboard_access(user):
    """Allow superusers and explicitly assigned, active staff moderators."""
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name=MODERATOR_GROUP_NAME).exists()
