"""Tests for the buyer-facing shop catalog (commerce.md §1, Week 17 / V2).

Covers banners, categories (with the synthetic "All"), product list filtering /
ordering / cursor pagination, and product detail — through the public HTTP stack.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.commerce.models import Category, Product, SellerStore, ShopBanner
from apps.identity.models import User


def _user(display_name: str = "Seller One") -> User:
    return User.objects.create(
        email=f"u-{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        display_name=display_name,
    )


def _store(owner: User) -> SellerStore:
    return SellerStore.objects.create(
        owner_user_id=owner.id,
        slug=f"store-{uuid.uuid4().hex[:8]}",
        name="Test Store",
    )


def _product(
    store: SellerStore,
    *,
    title: str = "Widget",
    price: str = "29.99",
    currency: str = "USD",
    status: str = Product.ACTIVE,
    category: Category | None = None,
    description: str = "",
    alternate_prices: dict | None = None,
    view_count: int = 0,
) -> Product:
    return Product.objects.create(
        store=store,
        category=category,
        title=title,
        description=description,
        price_amount=Decimal(price),
        price_currency=currency,
        alternate_prices=alternate_prices or {},
        stock=5,
        status=status,
        view_count=view_count,
    )


@pytest.mark.django_db
class TestShopBanners:
    def test_lists_active_banners_sorted(self):
        ShopBanner.objects.create(title="B", sort_order=2, is_active=True)
        ShopBanner.objects.create(title="A", sort_order=1, is_active=True)
        ShopBanner.objects.create(title="Hidden", sort_order=0, is_active=False)

        resp = APIClient().get("/api/v1/commerce/shop/banners")

        assert resp.status_code == 200
        results = resp.json()["results"]
        assert [b["title"] for b in results] == ["A", "B"]
        assert results[0]["action_type"] == "product"


@pytest.mark.django_db
class TestShopCategories:
    def test_synthetic_all_is_first_with_null_id(self):
        Category.objects.create(name="Romance", slug="romance", sort_order=1)
        Category.objects.create(name="Comedy", slug="comedy", sort_order=2)
        Category.objects.create(name="Hidden", slug="hidden", is_active=False)

        resp = APIClient().get("/api/v1/commerce/shop/categories")

        assert resp.status_code == 200
        results = resp.json()["results"]
        assert results[0] == {"id": None, "name": "All", "slug": "all"}
        assert [c["slug"] for c in results] == ["all", "romance", "comedy"]


@pytest.mark.django_db
class TestShopProductList:
    def test_lists_only_active_products_with_nested_shape(self):
        owner = _user("Jane")
        store = _store(owner)
        _product(
            store,
            title="Active",
            description="hi",
            alternate_prices={"MP": "3000.0000", "MC": "30.0000"},
        )
        _product(store, title="Draft", status=Product.DRAFT)
        _product(store, title="Archived", status=Product.ARCHIVED)

        resp = APIClient().get("/api/v1/commerce/shop/products")

        assert resp.status_code == 200
        body = resp.json()
        assert {p["title"] for p in body["results"]} == {"Active"}
        item = body["results"][0]
        assert item["price"] == {"amount": "29.9900", "currency": "USD"}
        assert item["alternate_prices"] == {"MP": "3000.0000", "MC": "30.0000"}
        assert item["stock_quantity"] == 5
        assert item["store"]["owner"] == {
            "id": str(owner.id),
            "display_name": "Jane",
            "avatar_url": None,
            "is_creator": False,
        }
        assert "cursor" in body

    def test_filter_by_category_slug(self):
        store = _store(_user())
        romance = Category.objects.create(name="Romance", slug="romance")
        _product(store, title="Loved", category=romance)
        _product(store, title="Other")

        resp = APIClient().get("/api/v1/commerce/shop/products?category=romance")

        assert [p["title"] for p in resp.json()["results"]] == ["Loved"]

    def test_category_all_returns_everything(self):
        store = _store(_user())
        _product(store, title="One")
        _product(store, title="Two")

        resp = APIClient().get("/api/v1/commerce/shop/products?category=all")

        assert len(resp.json()["results"]) == 2

    def test_search_matches_title_or_description(self):
        store = _store(_user())
        _product(store, title="Blue Mug", description="ceramic")
        _product(store, title="Red Hat", description="a blue brim")
        _product(store, title="Green Sock", description="wool")

        resp = APIClient().get("/api/v1/commerce/shop/products?q=blue")

        assert {p["title"] for p in resp.json()["results"]} == {"Blue Mug", "Red Hat"}

    def test_filter_by_seller(self):
        owner_a = _user()
        owner_b = _user()
        _product(_store(owner_a), title="A")
        _product(_store(owner_b), title="B")

        resp = APIClient().get(f"/api/v1/commerce/shop/products?seller_id={owner_a.id}")

        assert {p["title"] for p in resp.json()["results"]} == {"A"}

    def test_ordering_by_price(self):
        store = _store(_user())
        _product(store, title="Cheap", price="5.00")
        _product(store, title="Pricey", price="50.00")

        resp = APIClient().get("/api/v1/commerce/shop/products?ordering=price_amount")

        assert [p["title"] for p in resp.json()["results"]] == ["Cheap", "Pricey"]


@pytest.mark.django_db
class TestShopProductDetail:
    def test_detail_includes_html_and_timestamps(self):
        store = _store(_user())
        product = _product(store, description="hello")

        resp = APIClient().get(f"/api/v1/commerce/shop/products/{product.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == str(product.id)
        assert body["description_html"] == "hello"
        assert body["created_at"] is not None
        assert body["updated_at"] is not None

    def test_archived_product_is_not_found(self):
        store = _store(_user())
        product = _product(store, status=Product.ARCHIVED)

        resp = APIClient().get(f"/api/v1/commerce/shop/products/{product.id}")

        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "PRODUCT_NOT_FOUND"

    def test_missing_product_is_not_found(self):
        resp = APIClient().get(f"/api/v1/commerce/shop/products/{uuid.uuid4()}")

        assert resp.status_code == 404
