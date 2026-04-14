"""Tests unitaires pour src/core_services/base_service.py"""
import pytest
from unittest.mock import MagicMock, PropertyMock
from pathlib import Path

from src.core_services.base_service import BaseService
from src.core_services.contracts import ServiceContext


class ConcreteService(BaseService):
    """Service concret pour les tests."""
    def do_something(self):
        return "done"


@pytest.fixture
def ctx(tmp_path):
    mock_ctx = MagicMock(spec=ServiceContext)
    mock_ctx.data_dir = tmp_path
    mock_ctx.llm = MagicMock()
    mock_ctx.memory = MagicMock()
    return mock_ctx


class TestBaseService:
    def test_instantiation(self, ctx):
        svc = ConcreteService(ctx)
        assert svc.ctx is ctx

    def test_data_dir_property(self, ctx, tmp_path):
        svc = ConcreteService(ctx)
        assert svc.data_dir == tmp_path

    def test_llm_property(self, ctx):
        svc = ConcreteService(ctx)
        assert svc.llm is ctx.llm

    def test_memory_property(self, ctx):
        svc = ConcreteService(ctx)
        assert svc.memory is ctx.memory

    def test_concrete_subclass_works(self, ctx):
        svc = ConcreteService(ctx)
        assert svc.do_something() == "done"

    def test_base_service_requires_ctx(self):
        """BaseService nécessite un argument ctx."""
        with pytest.raises(TypeError):
            BaseService()
