"""Validated public inquiry and email subscription forms."""

from django import forms

from .models import ContactInquiry, NewsletterSubscription


class ContactInquiryForm(forms.ModelForm):
    """Collect only contact details needed to respond to a visitor."""

    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )

    class Meta:
        model = ContactInquiry
        fields = ("name", "email", "subject", "message", "consent_given")
        widgets = {
            "name": forms.TextInput(attrs={"autocomplete": "name", "maxlength": 120}),
            "email": forms.EmailInput(attrs={"autocomplete": "email", "maxlength": 254}),
            "subject": forms.TextInput(attrs={"maxlength": 180}),
            "message": forms.Textarea(attrs={"rows": 6, "maxlength": 4000}),
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
            raise forms.ValidationError("Please agree to the privacy notice.")
        return consent


class NewsletterForm(forms.ModelForm):
    """Normalize subscription addresses and require express consent."""

    website = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"tabindex": "-1", "autocomplete": "off"}),
    )

    class Meta:
        model = NewsletterSubscription
        fields = ("email", "consent_given")
        widgets = {
            "email": forms.EmailInput(
                attrs={"autocomplete": "email", "maxlength": 254}
            )
        }

    def clean_email(self):
        """Canonicalize case so differently-cased duplicates are handled once."""
        return self.cleaned_data["email"].strip().casefold()

    def clean_website(self):
        value = self.cleaned_data["website"].strip()
        if value:
            raise forms.ValidationError("Unable to accept this subscription.")
        return value

    def clean_consent_given(self):
        consent = self.cleaned_data["consent_given"]
        if not consent:
            raise forms.ValidationError(
                "Please consent to receive Summit email updates."
            )
        return consent
