from django.shortcuts import redirect
from django.urls import reverse

from .models import Subscription, get_or_create_account_for_user


class AccountContextMiddleware:
    """Resolves request.account, the tenant for all account-scoped queries."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            request.account = get_or_create_account_for_user(request.user)
        else:
            request.account = None

        return self.get_response(request)

EXEMPT_PATH_PREFIXES = (
    "/billing/",
    "/webhooks/",
    "/admin/",
    "/accounts/",
    "/signup/",
    "/verify-email/",
    "/redeem-code/",
    "/portal/",
    "/static/",
    "/media/",
    "/i18n/",
)

EXEMPT_PATHS = (
    "/manifest.json",
    "/sw.js",
)


class SubscriptionRequiredMiddleware:
    """Blocks app access for accounts without an active/trialing subscription."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_gated(request):
            # Only accounts that already have a Subscription row (created by
            # the signup flow going forward) are subject to this gate at all -
            # accounts predating this feature have no row and are unaffected.
            subscription = Subscription.objects.filter(user=request.user).first()
            if subscription is not None and not subscription.is_in_good_standing:
                target = "billing_start" if not subscription.external_customer_id else "billing_required"
                if request.path != reverse(target):
                    return redirect(target)

        return self.get_response(request)

    def _is_gated(self, request):
        if not request.user.is_authenticated or request.user.is_superuser:
            return False
        if request.path in EXEMPT_PATHS:
            return False
        return not request.path.startswith(EXEMPT_PATH_PREFIXES)
