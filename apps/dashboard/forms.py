"""Validated filters for dashboard tables and aggregate reports."""

from django import forms

from apps.registrations.models import Registration, PassType

from .services import local_date_boundary


class RegistrationFilterForm(forms.Form):
    """Filter the private dashboard registration table without exposing PII."""

    status = forms.ChoiceField(
        required=False,
        choices=(("", "All registration statuses"), *Registration.Status.choices),
    )
    payment_status = forms.ChoiceField(
        required=False,
        choices=(("", "All payment statuses"), *Registration.PaymentStatus.choices),
    )
    pass_category = forms.ChoiceField(
        required=False,
        choices=(("", "All pass categories"), *PassType.Category.choices),
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "End date must be on or after the start date.")
        return cleaned_data

    def filter_queryset(self, queryset):
        """Apply only validated filters to an already-authorized queryset."""
        if not self.is_valid():
            return queryset.none()
        data = self.cleaned_data
        if data.get("status"):
            queryset = queryset.filter(status=data["status"])
        if data.get("payment_status"):
            queryset = queryset.filter(payment_status=data["payment_status"])
        if data.get("pass_category"):
            queryset = queryset.filter(pass_type__category=data["pass_category"])
        if data.get("date_from"):
            queryset = queryset.filter(
                submitted_at__gte=local_date_boundary(data["date_from"])
            )
        if data.get("date_to"):
            queryset = queryset.filter(
                submitted_at__lt=local_date_boundary(data["date_to"], next_day=True)
            )
        return queryset


class ReportFilterForm(forms.Form):
    """Select an optional local-time date range for aggregate reports."""

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        date_from = cleaned_data.get("date_from")
        date_to = cleaned_data.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "End date must be on or after the start date.")
        return cleaned_data
