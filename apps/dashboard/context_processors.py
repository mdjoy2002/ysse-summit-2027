"""Admin-template context for showing the secondary dashboard link."""

from .access import has_dashboard_access


def dashboard_access(request):
    """Expose link visibility only; protected views repeat server-side checks."""
    return {
        "can_access_management_dashboard": has_dashboard_access(request.user)
    }
