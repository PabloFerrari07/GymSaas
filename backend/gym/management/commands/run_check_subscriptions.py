from django.core.management.base import BaseCommand

from gym.tasks import check_subscriptions


class Command(BaseCommand):
    help = "Ejecuta el job check_subscriptions de forma sincrona (sin Celery)"

    def handle(self, *args, **options):
        self.stdout.write("Corriendo check_subscriptions...")
        check_subscriptions()
        self.stdout.write(self.style.SUCCESS("Listo."))
