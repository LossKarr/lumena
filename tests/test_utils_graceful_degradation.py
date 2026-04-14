"""Tests unitaires pour src/utils/graceful_degradation.py"""
import pytest
from unittest.mock import patch, MagicMock

from src.utils.graceful_degradation import (
    ModuleStatus,
    DependencyReport,
    GracefulDegradation,
)


# ─── ModuleStatus ──────────────────────────────────────────────────────────

class TestModuleStatus:
    def test_available_module(self):
        ms = ModuleStatus(name="psutil", available=True, feature="system_monitoring")
        assert ms.available is True
        assert ms.error is None
        assert ms.fallback_available is True

    def test_unavailable_module(self):
        ms = ModuleStatus(name="missing_mod", available=False, feature="voice", error="No module")
        assert ms.available is False
        assert ms.error == "No module"


# ─── DependencyReport ──────────────────────────────────────────────────────

class TestDependencyReport:
    def _make(self, items):
        """items = [(name, available, feature)]"""
        modules = [ModuleStatus(name=n, available=a, feature=f) for n, a, f in items]
        return DependencyReport(modules=modules)

    def test_all_available_true(self):
        report = self._make([("m1", True, "f1"), ("m2", True, "f2")])
        assert report.all_available is True

    def test_all_available_false(self):
        report = self._make([("m1", True, "f1"), ("m2", False, "f2")])
        assert report.all_available is False

    def test_missing_list(self):
        report = self._make([("a", True, "x"), ("b", False, "y"), ("c", False, "z")])
        assert set(report.missing) == {"b", "c"}

    def test_features_degraded(self):
        report = self._make([
            ("m1", True, "voice"),
            ("m2", False, "browser"),
            ("m3", False, "browser"),
        ])
        assert "browser" in report.features_degraded
        assert "voice" not in report.features_degraded

    def test_summary_all_ok(self):
        report = self._make([("m1", True, "f1")])
        summary = report.summary()
        assert "Toutes" in summary or "disponibles" in summary

    def test_summary_with_missing(self):
        report = self._make([("m1", True, "f1"), ("m2", False, "f2")])
        summary = report.summary()
        assert "manquantes" in summary or "missing" in summary.lower() or "f2" in summary

    def test_empty_report(self):
        report = DependencyReport()
        assert report.all_available is True
        assert report.missing == []


# ─── GracefulDegradation singleton ─────────────────────────────────────────

class TestGracefulDegradationSingleton:
    def test_singleton_returns_same_instance(self):
        gd1 = GracefulDegradation()
        gd2 = GracefulDegradation()
        assert gd1 is gd2


# ─── GracefulDegradation.check_module ──────────────────────────────────────

class TestGracefulDegradationCheckModule:
    def setup_method(self):
        # Reset state each time via new instance cache
        gd = GracefulDegradation()
        gd._module_cache = {}
        gd._report = None

    def test_existing_module_available(self):
        gd = GracefulDegradation()
        gd._module_cache = {}
        result = gd.check_module("os")
        assert result is True

    def test_missing_module_unavailable(self):
        gd = GracefulDegradation()
        gd._module_cache = {}
        result = gd.check_module("this_module_does_not_exist_xyz_abc")
        assert result is False

    def test_result_is_cached(self):
        gd = GracefulDegradation()
        gd._module_cache = {}
        gd.check_module("os")
        assert "os" in gd._module_cache
        assert gd._module_cache["os"] is True


# ─── GracefulDegradation.is_feature_available ──────────────────────────────

class TestGracefulDegradationFeature:
    def setup_method(self):
        gd = GracefulDegradation()
        gd._module_cache = {}

    def test_unknown_feature_returns_true(self):
        gd = GracefulDegradation()
        # Feature not in MODULE_FEATURES → available by default
        result = gd.is_feature_available("nonexistent_feature_xyz")
        assert result is True

    def test_system_monitoring_with_psutil(self):
        gd = GracefulDegradation()
        gd._module_cache = {"psutil": True}
        result = gd.is_feature_available("system_monitoring")
        assert result is True

    def test_feature_unavailable_when_all_modules_missing(self):
        gd = GracefulDegradation()
        gd._module_cache = {}
        # Patch check_module to always return False
        with patch.object(gd, "check_module", return_value=False):
            result = gd.is_feature_available("memory")
        assert result is False


# ─── GracefulDegradation.check_all ─────────────────────────────────────────

class TestGracefulDegradationCheckAll:
    def test_check_all_returns_dependency_report(self):
        gd = GracefulDegradation()
        gd._module_cache = {}
        report = gd.check_all()
        assert isinstance(report, DependencyReport)
        assert len(report.modules) > 0

    def test_report_cached(self):
        gd = GracefulDegradation()
        gd._module_cache = {}
        gd._report = None
        report1 = gd.check_all()
        report2 = gd.get_report()
        assert report1 is report2
