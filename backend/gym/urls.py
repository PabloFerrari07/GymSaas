from django.urls import path

from . import views

urlpatterns = [
    path("webhooks/whatsapp/", views.whatsapp_webhook, name="whatsapp_webhook"),
]
