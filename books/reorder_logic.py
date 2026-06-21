import math
from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from .models import Sale


REORDER_VELOCITY_WINDOW_DAYS = 30
REORDER_COVER_DAYS = 30


def daily_sales_velocity(book):
    cutoff = timezone.now().date() - timedelta(days=REORDER_VELOCITY_WINDOW_DAYS)

    units_sold = Sale.objects.filter(
        book=book, sale_date__gte=cutoff
    ).aggregate(total=Sum("quantity"))["total"] or 0

    return units_sold / REORDER_VELOCITY_WINDOW_DAYS


def suggested_reorder_quantity(book, velocity=None):
    if velocity is None:
        velocity = daily_sales_velocity(book)

    needed_for_cover = math.ceil(velocity * REORDER_COVER_DAYS) - book.stock_on_hand

    if needed_for_cover > 0:
        return needed_for_cover

    return max(book.reorder_threshold * 2 - book.stock_on_hand, book.reorder_threshold, 1)
