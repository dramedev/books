from django.db.models import DecimalField, ExpressionWrapper, F


REVENUE_EXPRESSION = ExpressionWrapper(
    F("quantity") * F("unit_price"),
    output_field=DecimalField(max_digits=10, decimal_places=2),
)

PURCHASE_COST_EXPRESSION = ExpressionWrapper(
    F("quantity") * F("unit_cost"),
    output_field=DecimalField(max_digits=10, decimal_places=2),
)
