from django.urls import path

from . import views


urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("verify-email/", views.verify_email, name="verify_email"),
    path("redeem-code/", views.redeem_access_code, name="redeem_access_code"),
    path("", views.dashboard, name="dashboard"),
    path("profile/", views.profile_update, name="profile_update"),
    path("books/", views.book_list, name="book_list"),
    path("book/<int:id>/", views.book_detail, name="book_detail"),
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
    path("about/", views.about, name="about"),
    path("chat/", views.chat_api, name="chat_api"),
    path("export/csv/", views.export_books_csv, name="export_books_csv"),
    path("export/excel/", views.export_books_excel, name="export_books_excel"),
    path("export/pdf/", views.export_books_pdf, name="export_books_pdf"),
    path("sales/export/csv/", views.export_sales_csv, name="export_sales_csv"),
    path("sales/export/excel/", views.export_sales_excel, name="export_sales_excel"),
    path("sales/export/pdf/", views.export_sales_pdf, name="export_sales_pdf"),
]
