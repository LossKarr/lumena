from pathlib import Path
import asyncio
import os
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.channels.telegram_channel as telegram_channel_module
from src.channels.telegram_channel import TelegramChannel
from src.utils.file_lock import ProcessFileLock
from telegram.error import Conflict
from telegram.error import TimedOut


class _FakeUpdater:
    def __init__(self, conflict_on_start: bool = False):
        self.conflict_on_start = conflict_on_start
        self.polling_started = False
        self.polling_stopped = False
        self.error_callback = None

    async def start_polling(self, **kwargs):
        self.polling_started = True
        self.error_callback = kwargs.get("error_callback")
        if self.conflict_on_start and self.error_callback:
            self.error_callback(Conflict("terminated by other getUpdates request"))

    async def stop(self):
        self.polling_stopped = True


class _FakeApp:
    def __init__(self, conflict_on_start: bool = False, startup_errors=None):
        self.updater = _FakeUpdater(conflict_on_start=conflict_on_start)
        self.stopped = False
        self.shutdown_called = False
        self.startup_errors = list(startup_errors or [])

    def add_handler(self, _handler):
        return None

    async def initialize(self):
        if self.startup_errors:
            raise self.startup_errors.pop(0)
        return None

    async def start(self):
        return None

    async def stop(self):
        self.stopped = True

    async def shutdown(self):
        self.shutdown_called = True


def _make_fake_application_class(app: _FakeApp):
    class _FakeApplicationClass:
        @staticmethod
        def builder():
            class _Builder:
                def token(self, _token):
                    return self

                def build(self):
                    return app

            return _Builder()

    return _FakeApplicationClass


@pytest.mark.asyncio
async def test_telegram_channel_disables_itself_on_bootstrap_conflict(monkeypatch, tmp_path):
    lock_path = tmp_path / "telegram.lock"
    monkeypatch.setenv("LUMENA_TELEGRAM_LOCK_PATH", str(lock_path))
    monkeypatch.delenv("LUMENA_DISABLE_TELEGRAM", raising=False)

    app = _FakeApp(conflict_on_start=True)
    monkeypatch.setattr(
        telegram_channel_module,
        "Application",
        _make_fake_application_class(app),
    )

    channel = TelegramChannel(token="dummy-token")
    started = await channel.start()

    assert started is False
    await asyncio.sleep(0)
    assert channel.is_running is False
    assert channel.conflict_seen is True
    assert channel.state == "disabled_conflict"


@pytest.mark.asyncio
async def test_telegram_channel_stops_when_conflict_happens_after_start(monkeypatch, tmp_path):
    lock_path = tmp_path / "telegram.lock"
    monkeypatch.setenv("LUMENA_TELEGRAM_LOCK_PATH", str(lock_path))
    monkeypatch.delenv("LUMENA_DISABLE_TELEGRAM", raising=False)

    app = _FakeApp(conflict_on_start=False)
    monkeypatch.setattr(
        telegram_channel_module,
        "Application",
        _make_fake_application_class(app),
    )

    channel = TelegramChannel(token="dummy-token")
    started = await channel.start()

    assert started is True
    assert channel.is_running is True
    assert app.updater.error_callback is not None

    app.updater.error_callback(Conflict("terminated by other getUpdates request"))
    await asyncio.sleep(0)

    assert channel.is_running is False
    assert channel.conflict_seen is True
    assert channel.state == "disabled_conflict"


@pytest.mark.asyncio
async def test_telegram_channel_refuses_start_if_lock_is_already_held(monkeypatch, tmp_path):
    lock_path = tmp_path / "telegram.lock"
    monkeypatch.setenv("LUMENA_TELEGRAM_LOCK_PATH", str(lock_path))
    monkeypatch.delenv("LUMENA_DISABLE_TELEGRAM", raising=False)

    owner_lock = ProcessFileLock(lock_path, lock_name="test-owner", owner_id="owner")
    assert owner_lock.acquire() is True

    try:
        app = _FakeApp(conflict_on_start=False)
        monkeypatch.setattr(
            telegram_channel_module,
            "Application",
            _make_fake_application_class(app),
        )

        channel = TelegramChannel(token="dummy-token")
        started = await channel.start()

        assert started is False
        assert channel.is_running is False
        assert channel.conflict_seen is True
        assert channel.state == "disabled_conflict"
        assert "lock" in (channel.last_error or "")
    finally:
        owner_lock.release()


@pytest.mark.asyncio
async def test_telegram_stop_is_idempotent(monkeypatch, tmp_path):
    lock_path = tmp_path / "telegram.lock"
    monkeypatch.setenv("LUMENA_TELEGRAM_LOCK_PATH", str(lock_path))
    monkeypatch.delenv("LUMENA_DISABLE_TELEGRAM", raising=False)

    app = _FakeApp(conflict_on_start=False)
    monkeypatch.setattr(
        telegram_channel_module,
        "Application",
        _make_fake_application_class(app),
    )

    channel = TelegramChannel(token="dummy-token")
    started = await channel.start()

    assert started is True
    await channel.stop()
    await channel.stop()

    assert channel.is_running is False
    assert channel.state == "stopped"


@pytest.mark.asyncio
async def test_telegram_channel_retries_timeout_startup(monkeypatch, tmp_path):
    lock_path = tmp_path / "telegram.lock"
    monkeypatch.setenv("LUMENA_TELEGRAM_LOCK_PATH", str(lock_path))
    monkeypatch.delenv("LUMENA_DISABLE_TELEGRAM", raising=False)
    monkeypatch.setenv("LUMENA_TELEGRAM_STARTUP_RETRIES", "1")
    monkeypatch.setenv("LUMENA_TELEGRAM_STARTUP_RETRY_DELAY", "0")

    app = _FakeApp(conflict_on_start=False, startup_errors=[TimedOut("Timed out")])
    monkeypatch.setattr(
        telegram_channel_module,
        "Application",
        _make_fake_application_class(app),
    )

    channel = TelegramChannel(token="dummy-token")
    started = await channel.start()

    assert started is True
    assert channel.is_running is True
    assert channel.state == "running"
    assert channel.last_error is None

    await channel.stop()


@pytest.mark.asyncio
async def test_telegram_channel_marks_transient_polling_errors_without_conflict(monkeypatch, tmp_path):
    lock_path = tmp_path / "telegram.lock"
    monkeypatch.setenv("LUMENA_TELEGRAM_LOCK_PATH", str(lock_path))
    monkeypatch.delenv("LUMENA_DISABLE_TELEGRAM", raising=False)

    app = _FakeApp(conflict_on_start=False)
    monkeypatch.setattr(
        telegram_channel_module,
        "Application",
        _make_fake_application_class(app),
    )

    channel = TelegramChannel(token="dummy-token")
    started = await channel.start()

    assert started is True
    assert app.updater.error_callback is not None

    app.updater.error_callback(TimedOut("Timed out"))
    status = channel.get_runtime_status()
    assert status["running"] is True
    assert status["transient_error"] is True
    assert status["transient_backoff_sec"] > 0
    assert status["state"] in {"running_degraded", "running"}
    assert "transient" in (status["last_error"] or "").lower()

    await channel.stop()
