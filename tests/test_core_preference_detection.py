from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.core import LumenaCore
from src.core_services.agent_service import AgentService


class _MemoryStub:
    def __init__(self):
        self.facts = {}

    def learn_fact(self, key: str, value: str):
        self.facts[key] = value

    def get_fact(self, key: str):
        return self.facts.get(key)

    def get_all_facts(self):
        return dict(self.facts)


def _build_core_stub() -> LumenaCore:
    core = LumenaCore.__new__(LumenaCore)
    core.memory = _MemoryStub()
    core._agent_svc = AgentService(core)
    return core


def test_detect_preferences_extracts_name_from_je_mappelle():
    core = _build_core_stub()

    core._detect_and_save_preferences("je m'appelle Alice")

    assert core.memory.facts["prénom_utilisateur"] == "Alice"


def test_detect_preferences_extracts_accented_name_from_cest():
    core = _build_core_stub()

    core._detect_and_save_preferences("Moi c'est Élodie")

    assert core.memory.facts["prénom_utilisateur"] == "Élodie"


def test_detect_preferences_ignores_regular_sentence():
    core = _build_core_stub()

    core._detect_and_save_preferences("yo test")

    assert "user_name" not in core.memory.facts
