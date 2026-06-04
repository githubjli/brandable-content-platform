"""Pytest configuration for the Django test suite."""

import django
import pytest
from django.test import RequestFactory


@pytest.fixture
def rf() -> RequestFactory:
    return RequestFactory()
