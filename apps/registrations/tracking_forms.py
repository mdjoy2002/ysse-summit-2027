"""Public registration lookup using the reference and an attendee contact."""

from django import forms


class RegistrationTrackingForm(forms.Form):
    reference = forms.CharField(max_length=36, label="Registration ID")
    email = forms.CharField(
        max_length=254,
        label="Email or phone number used for registration",
    )
