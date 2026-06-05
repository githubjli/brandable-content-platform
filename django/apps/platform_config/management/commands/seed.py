"""Seed management command — stub for Week 1."""

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Seed the database with initial data (not implemented yet)"

    def handle(self, *args, **options) -> None:
        self.stdout.write("seed not implemented yet")
