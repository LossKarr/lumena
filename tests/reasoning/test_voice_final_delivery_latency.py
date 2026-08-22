from types import SimpleNamespace

from src.reasoning.react import ReActLoop


def test_voice_final_delivery_skips_visual_typing_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr("time.sleep", lambda seconds: calls.append(seconds))
    loop = ReActLoop(runtime_ctx=SimpleNamespace(channel="voice"))
    loop._stream_and_return_final("Une reponse assez longue pour plusieurs morceaux.")
    assert calls == []


def test_web_final_delivery_keeps_historical_typing_sleep(monkeypatch):
    calls = []
    monkeypatch.setattr("time.sleep", lambda seconds: calls.append(seconds))
    loop = ReActLoop(runtime_ctx=SimpleNamespace(channel="web"))
    loop._stream_and_return_final("Une reponse assez longue pour plusieurs morceaux.")
    assert calls

