"""Tests for gift_totals aggregation + per-content gift_amount surfacing.

Resolves the deferred debt where content cards hardcoded gift_amount "0.0000".
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from rest_framework.test import APIClient

from apps.content.drama.models import DramaSeries
from apps.content.gift import services as gift_services
from apps.content.gift.models import GiftTransaction
from apps.content.video.models import Video


def _gift(target_type: str, target_id: str, amount: str, currency: str = "MP") -> GiftTransaction:
    return GiftTransaction.objects.create(
        idempotency_key=f"g-{uuid.uuid4().hex}",
        sender_id=uuid.uuid4(),
        receiver_id=uuid.uuid4(),
        target_type=target_type,
        target_id=target_id,
        amount=Decimal(amount),
        currency=currency,
        payment_method="meow_points" if currency == "MP" else "meow_credit",
    )


@pytest.mark.django_db
class TestGiftTotals:
    def test_sums_per_target_in_currency(self):
        v1, v2 = str(uuid.uuid4()), str(uuid.uuid4())
        _gift(GiftTransaction.VIDEO, v1, "10.0000")
        _gift(GiftTransaction.VIDEO, v1, "5.5000")
        _gift(GiftTransaction.VIDEO, v2, "3.0000")
        totals = gift_services.gift_totals(target_type=GiftTransaction.VIDEO, target_ids=[v1, v2])
        assert totals[v1] == "15.5000"
        assert totals[v2] == "3.0000"

    def test_filters_by_currency_and_omits_empty(self):
        v = str(uuid.uuid4())
        _gift(GiftTransaction.VIDEO, v, "10.0000", currency="MP")
        _gift(GiftTransaction.VIDEO, v, "99.0000", currency="MC")  # different currency
        totals = gift_services.gift_totals(target_type=GiftTransaction.VIDEO, target_ids=[v])
        assert totals[v] == "10.0000"  # MC excluded
        # A target with no MP gifts is omitted (caller defaults to 0).
        assert (
            gift_services.gift_totals(
                target_type=GiftTransaction.VIDEO, target_ids=[str(uuid.uuid4())]
            )
            == {}
        )

    def test_empty_ids(self):
        assert gift_services.gift_totals(target_type=GiftTransaction.VIDEO, target_ids=[]) == {}

    def test_target_type_constants_match_model(self):
        assert gift_services.TARGET_VIDEO == GiftTransaction.VIDEO
        assert gift_services.TARGET_DRAMA_SERIES == GiftTransaction.DRAMA_SERIES
        assert gift_services.TARGET_LIVE_STREAM == GiftTransaction.LIVE_STREAM


@pytest.mark.django_db
class TestContentSurfacesGiftAmount:
    def test_video_detail_shows_gift_total(self):
        video = Video.objects.create(owner_user_id=uuid.uuid4(), title="V")
        _gift(GiftTransaction.VIDEO, str(video.id), "12.0000")
        _gift(GiftTransaction.VIDEO, str(video.id), "8.0000")
        resp = APIClient().get(f"/api/v1/content/video/public/{video.id}")
        assert resp.status_code == 200
        assert resp.json()["counts"]["gift_amount"] == "20.0000"

    def test_drama_detail_shows_gift_total(self):
        series = DramaSeries.objects.create(owner_user_id=uuid.uuid4(), title="D")
        _gift(GiftTransaction.DRAMA_SERIES, str(series.id), "7.2500")
        resp = APIClient().get(f"/api/v1/content/drama/series/{series.id}")
        assert resp.status_code == 200
        assert resp.json()["counts"]["gift_amount"] == "7.2500"

    def test_ungifted_content_defaults_zero(self):
        video = Video.objects.create(owner_user_id=uuid.uuid4(), title="V")
        resp = APIClient().get(f"/api/v1/content/video/public/{video.id}")
        assert resp.json()["counts"]["gift_amount"] == "0.0000"
