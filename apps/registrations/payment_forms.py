"""Participant form for submitting manual mobile payment evidence."""

from django import forms

from .models import Registration


class ManualPaymentSubmissionForm(forms.ModelForm):
    class Meta:
        model = Registration
        fields = (
            "manual_payment_method",
            "payment_transaction_id",
            "payment_proof",
        )
        widgets = {
            "payment_transaction_id": forms.TextInput(
                attrs={"autocomplete": "off", "maxlength": 100}
            ),
            "payment_proof": forms.ClearableFileInput(
                attrs={"accept": ".jpg,.jpeg,.png,.webp,image/jpeg,image/png,image/webp"}
            ),
        }
        labels = {
            "manual_payment_method": "Mobile banking service",
            "payment_transaction_id": "Transaction ID",
            "payment_proof": "Payment screenshot",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["manual_payment_method"].required = True
        self.fields["payment_transaction_id"].required = True
        self.fields["payment_proof"].required = True
