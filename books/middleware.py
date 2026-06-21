from django.shortcuts import redirect
from django.urls import reverse

from .models import AccountMembership, Subscription, get_or_create_account_for_user


class AccountContextMiddleware:
    """Resolves request.account/request.is_account_admin, the tenant context
    for all account-scoped queries and the "Team" nav link's admin gating."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            request.account = get_or_create_account_for_user(request.user)
            request.is_account_admin = AccountMembership.objects.filter(
                account=request.account, user=request.user, role=AccountMembership.ROLE_ADMIN,
            ).exists()
        else:
            request.account = None
            request.is_account_admin = False

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
            # Gating by account (not user) means a lapsed subscription blocks
            # every member of the account, not just whoever first subscribed.
            subscription = Subscription.objects.filter(account=request.account).first()
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
