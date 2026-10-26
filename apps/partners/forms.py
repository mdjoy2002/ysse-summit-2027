"""Validated public partnership inquiry form."""

from django import forms

from .models import PartnershipInquiry


class PartnershipInquiryForm(forms.ModelForm):
    """Collect only contact and partnership details needed for follow-up."""

    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )

    class Meta:
        model = PartnershipInquiry
        fields = (
            "organization_name",
            "contact_name",
            "business_email",
            "phone_number",
            "interest",
            "message",
            "consent_given",
        )
        widgets = {
            "organization_name": forms.TextInput(
                attrs={"autocomplete": "organization", "maxlength": 160}
            ),
            "contact_name": forms.TextInput(
                attrs={"autocomplete": "name", "maxlength": 120}
            ),
            "business_email": forms.EmailInput(
                attrs={"autocomplete": "email", "maxlength": 254}
            ),
            "phone_number": forms.TextInput(
                attrs={"autocomplete": "tel", "maxlength": 32}
            ),
            "message": forms.Textarea(attrs={"rows": 5, "maxlength": 3000}),
        }

    def clean_website(self):
        """Reject automated submissions that populate the hidden honeypot."""
        value = self.cleaned_data["website"].strip()
        if value:
            raise forms.ValidationError("Unable to accept this submission.")
        return value

    def clean_consent_given(self):
        consent = self.cleaned_data["consent_given"]
        if not consent:
            raise forms.ValidationError(
                "Please agree to the privacy notice before submitting."
            )
        return consent
