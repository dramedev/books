from django.urls import path

from . import views


urlpatterns = [
    path("", views.book_list, name="book_list"),
    path("add/", views.book_create, name="book_create"),
    path("edit/<int:id>/", views.book_update, name="book_update"),
    path("delete/<int:id>/", views.book_delete, name="book_delete"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/add/", views.category_create, name="category_create"),
    path("categories/edit/<int:id>/", views.category_update, name="category_update"),
    path("categories/delete/<int:id>/", views.category_delete, name="category_delete"),
    path("report/", views.report, name="report"),
    path("export/csv/", views.export_books_csv, name="export_books_csv"),
    path("export/excel/", views.export_books_excel, name="export_books_excel"),
    path("export/pdf/", views.export_books_pdf, name="export_books_pdf"),
]
