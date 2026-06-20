from django.contrib.auth.models import Group, Permission


ROLE_PERMISSIONS = {
    "Admin": [
        "add_book", "change_book", "delete_book", "view_book",
        "add_category", "change_category", "delete_category", "view_category",
        "add_author", "change_author", "delete_author", "view_author",
        "add_sale", "change_sale", "delete_sale", "view_sale",
        "add_reorder", "change_reorder", "delete_reorder", "view_reorder",
        "add_supplier", "change_supplier", "delete_supplier", "view_supplier",
        "add_return", "change_return", "delete_return", "view_return",
        "add_stockadjustment", "change_stockadjustment", "delete_stockadjustment", "view_stockadjustment",
        "add_invoice", "change_invoice", "delete_invoice", "view_invoice",
        "add_invoiceitem", "change_invoiceitem", "delete_invoiceitem", "view_invoiceitem",
        "add_printrun", "change_printrun", "delete_printrun", "view_printrun",
        "add_royaltyrate", "change_royaltyrate", "delete_royaltyrate", "view_royaltyrate",
        "add_royaltypayment", "change_royaltypayment", "delete_royaltypayment", "view_royaltypayment",
        "add_location", "change_location", "delete_location", "view_location",
        "add_stocklevel", "change_stocklevel", "delete_stocklevel", "view_stocklevel",
        "add_integration", "change_integration", "delete_integration", "view_integration",
        "add_customer", "change_customer", "delete_customer", "view_customer",
    ],
    "Staff": [
        "add_book", "change_book", "view_book",
        "add_category", "change_category", "view_category",
        "add_author", "change_author", "view_author",
        "add_sale", "change_sale", "view_sale",
        "add_reorder", "change_reorder", "view_reorder",
        "add_supplier", "change_supplier", "view_supplier",
        "add_return", "change_return", "view_return",
        "add_stockadjustment", "change_stockadjustment", "view_stockadjustment",
        "add_invoice", "change_invoice", "view_invoice",
        "add_invoiceitem", "change_invoiceitem", "view_invoiceitem",
        "add_printrun", "view_printrun",
        "view_royaltyrate",
        "view_royaltypayment",
        "add_location", "change_location", "view_location",
        "view_stocklevel",
        "view_integration",
        "add_customer", "change_customer", "view_customer",
    ],
    "Viewer": [
        "view_book", "view_category", "view_author", "view_sale",
        "view_reorder", "view_supplier", "view_return", "view_stockadjustment",
        "view_invoice", "view_printrun", "view_royaltyrate", "view_royaltypayment",
        "view_location", "view_stocklevel", "view_integration",
        "view_customer",
    ],
}


def ensure_roles():
    groups = {}

    for role, codenames in ROLE_PERMISSIONS.items():
        group, _ = Group.objects.get_or_create(name=role)

        permissions = Permission.objects.filter(
            content_type__app_label="books",
            codename__in=codenames,
        )

        group.permissions.set(permissions)
        groups[role] = group

    return groups
