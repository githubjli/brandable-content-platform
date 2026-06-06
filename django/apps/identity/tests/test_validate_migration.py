"""Tests for validate_migration's pure check + aggregation logic (no DB needed)."""

from __future__ import annotations

from apps.identity.management.commands.validate_migration import (
    ValidationReport,
    count_match_check,
    skipped_check,
    zero_check,
)


class TestCheckBuilders:
    def test_count_match(self):
        assert count_match_check("c", 10, 10).passed is True
        bad = count_match_check("c", 10, 9)
        assert bad.passed is False
        assert bad.status == "FAIL"

    def test_zero_check(self):
        assert zero_check("z", 0).passed is True
        assert zero_check("z", 3).passed is False

    def test_skipped(self):
        s = skipped_check("w", "not run")
        assert s.skipped is True
        assert s.status == "SKIP"


class TestAggregation:
    def test_all_pass_is_pass(self):
        report = ValidationReport(started_at="t")
        report.checks = [count_match_check("a", 1, 1), zero_check("b", 0)]
        assert report.status == "PASS"

    def test_any_fail_is_fail(self):
        report = ValidationReport(started_at="t")
        report.checks = [count_match_check("a", 1, 1), zero_check("b", 5)]
        assert report.status == "FAIL"

    def test_skipped_does_not_fail_gate(self):
        report = ValidationReport(started_at="t")
        report.checks = [count_match_check("a", 1, 1), skipped_check("w", "skipped")]
        assert report.status == "PASS"

    def test_report_dict_shape(self):
        report = ValidationReport(started_at="t")
        report.checks = [count_match_check("a", 2, 2)]
        d = report.to_dict()
        assert d["command"] == "validate_migration"
        assert d["status"] == "PASS"
        assert d["checks"][0]["status"] == "PASS"
