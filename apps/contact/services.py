"""Rate limiting and double-opt-in newsletter workflows."""

import hashlib
import uuid

from django.conf import settings
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from .models import NewsletterSubscription


RATE_LIMIT = 5
RATE_WINDOW_SECONDS = 60 * 60


def rate_limit_exceeded(namespace, remote_address):
    """Limit public form requests without retaining a visitor's raw IP address."""
    address_digest = hashlib.sha256(remote_address.encode("utf-8")).hexdigest()
    cache_key = f"contact:{namespace}:{address_digest}"
    if cache.add(cache_key, 1, timeout=RATE_WINDOW_SECONDS):
        return False
    return cache.incr(cache_key) > RATE_LIMIT


def subscribe_to_newsletter(*, email, consent_given, site_origin):
    """Create or re-activate a pending subscription and send double opt-in mail."""
    subscription, created = NewsletterSubscription.objects.get_or_create(
        email=email,
        defaults={"consent_given": consent_given},
    )
    if not created and subscription.status == NewsletterSubscription.Status.ACTIVE:
        return subscription, False
    if not created:
        subscription.status = NewsletterSubscription.Status.PENDING
        subscription.consent_given = consent_given
        subscription.confirmation_token = uuid.uuid4()
        subscription.subscribed_at = timezone.now()
        subscription.confirmed_at = None
        subscription.unsubscribed_at = None
        subscription.save(
            update_fields=(
                "status",
                "consent_given",
                "confirmation_token",
                "subscribed_at",
                "confirmed_at",
                "unsubscribed_at",
            )
        )
    confirmation_url = site_origin.rstrip("/") + reverse(
        "contact:newsletter-confirm",
        args=(subscription.confirmation_token,),
    )
    unsubscribe_url = site_origin.rstrip("/") + reverse(
        "contact:newsletter-unsubscribe",
        args=(subscription.unsubscribe_token,),
    )
    send_mail(
        subject="Confirm your YSSE Summit newsletter subscription",
        message=(
            "Please confirm your request to receive YSSE Summit updates by "
            f"opening this link:\n\n{confirmation_url}\n\n"
            "If you did not request updates, you can ignore this email.\n\n"
            f"Unsubscribe at any time: {unsubscribe_url}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[subscription.email],
        fail_silently=False,
    )
    return subscription, True


def confirm_newsletter_subscription(token):
    """Activate a pending subscription only through its emailed token."""
    with transaction.atomic():
        subscription = NewsletterSubscription.objects.select_for_update().filter(
            confirmation_token=token
        ).first()
        if not subscription or subscription.status != NewsletterSubscription.Status.PENDING:
            return False
        subscription.status = NewsletterSubscription.Status.ACTIVE
        subscription.confirmed_at = timezone.now()
        subscription.save(update_fields=("status", "confirmed_at"))
        return True


def unsubscribe_from_newsletter(token):
    """Deactivate a subscriber using a random, private unsubscribe token."""
    with transaction.atomic():
        subscription = NewsletterSubscription.objects.select_for_update().filter(
            unsubscribe_token=token
        ).first()
        if not subscription or subscription.status == NewsletterSubscription.Status.UNSUBSCRIBED:
            return False
        subscription.status = NewsletterSubscription.Status.UNSUBSCRIBED
        subscription.unsubscribed_at = timezone.now()
        subscription.save(update_fields=("status", "unsubscribed_at"))
        return True
