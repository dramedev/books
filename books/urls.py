from django.urls import path

from . import views


urlpatterns = [
    path("", views.book_list, name="book_list"),
    path("add/", views.book_create, name="book_create"),
    path("edit/<int:id>/", views.book_update, name="book_update"),
    path("delete/<int:id>/", views.book_delete, name="book_delete"),
    path("stock/", views.stock_list, name="stock_list"),
    path("categories/", views.category_list, name="category_list"),
    path("categories/add/", views.category_create, name="category_create"),
    path("categories/edit/<int:id>/", views.category_update, name="category_update"),
    path("categories/delete/<int:id>/", views.category_delete, name="category_delete"),
    path("authors/", views.author_list, name="author_list"),
    path("authors/add/", views.author_create, name="author_create"),
    path("authors/edit/<int:id>/", views.author_update, name="author_update"),
    path("authors/delete/<int:id>/", views.author_delete, name="author_delete"),
    path("sales/", views.sale_list, name="sale_list"),
    path("sales/add/", views.sale_create, name="sale_create"),
    path("sales/edit/<int:id>/", views.sale_update, name="sale_update"),
    path("sales/delete/<int:id>/", views.sale_delete, name="sale_delete"),
    path("report/", views.report, name="report"),
    path("export/csv/", views.export_books_csv, name="export_books_csv"),
    path("export/excel/", views.export_books_excel, name="export_books_excel"),
    path("export/pdf/", views.export_books_pdf, name="export_books_pdf"),
]
