from django.contrib.auth.models import Group, Permission


ROLE_PERMISSIONS = {
    "Admin": [
        "add_book",
        "change_book",
        "delete_book",
        "view_book",
        "add_category",
        "change_category",
        "delete_category",
        "view_category",
        "add_author",
        "change_author",
        "delete_author",
        "view_author",
        "add_sale",
        "change_sale",
        "delete_sale",
        "view_sale",
        "add_reorder",
        "change_reorder",
        "delete_reorder",
        "view_reorder",
        "add_supplier",
        "change_supplier",
        "delete_supplier",
        "view_supplier",
        "add_return",
        "change_return",
        "delete_return",
        "view_return",
    ],
    "Staff": [
        "add_book",
        "change_book",
        "view_book",
        "add_category",
        "change_category",
        "view_category",
        "add_author",
        "change_author",
        "view_author",
        "add_sale",
        "change_sale",
        "view_sale",
        "add_reorder",
        "change_reorder",
        "view_reorder",
        "add_supplier",
        "change_supplier",
        "view_supplier",
        "add_return",
        "change_return",
        "view_return",
    ],
    "Viewer": [
        "view_book",
        "view_category",
        "view_author",
        "view_sale",
        "view_reorder",
        "view_supplier",
        "view_return",
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
