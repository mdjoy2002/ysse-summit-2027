"""Validated participant forms for summit registration."""

from django import forms
from django.utils import timezone

from apps.home.models import SummitEvent

from .models import PassType, Registration


class RegistrationForm(forms.ModelForm):
    """Collect only attendee details needed to issue and administer a pass."""

    consent = forms.BooleanField(
        label="I agree to the summit team using these details to administer my registration.",
        required=True,
    )

    class Meta:
        model = Registration
        fields = ("pass_type", "full_name", "contact_email", "phone_number")
        widgets = {
            "full_name": forms.TextInput(attrs={"autocomplete": "name"}),
            "contact_email": forms.EmailInput(attrs={"autocomplete": "email"}),
            "phone_number": forms.TelInput(attrs={"autocomplete": "tel"}),
        }

    def __init__(self, *args, event=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.event = event
        available_pass_ids = [
            pass_type.pk
            for pass_type in PassType.objects.filter(
                is_published=True, is_available=True
            )
            if pass_type.sales_are_open() and pass_type.has_capacity
        ]
        self.fields["pass_type"].queryset = PassType.objects.filter(
            pk__in=available_pass_ids
        )

    def clean_pass_type(self):
        """Recheck the sale window and publication state at form submission."""
        pass_type = self.cleaned_data["pass_type"]
        if not pass_type.sales_are_open(timezone.now()):
            raise forms.ValidationError("This pass is no longer available.")
        if self.event is None or not self.event.registration_available:
            raise forms.ValidationError("Summit registration is currently closed.")
        if self.event.status in (SummitEvent.Status.CANCELLED, SummitEvent.Status.COMPLETED):
            raise forms.ValidationError("Registration is unavailable for this event.")
        return pass_type
