"""Tests du token streaming FINAL_ANSWER (P3)."""
from pathlib import Path
import sys
import re

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Test 1: Le code de streaming est bien présent dans react.py ──

def test_final_token_emitted_in_react():
    """react.py contient la boucle [FINAL_TOKEN] avant le return."""
    import inspect
    from src.reasoning import react as react_mod
    source = inspect.getsource(react_mod)
    assert "[FINAL_TOKEN]" in source


def test_final_token_chunking_logic():
    """Le chunking par 4 mots préserve les newlines."""
    message = "Bonjour ceci est un test\nde token streaming pour\nla réponse finale"
    # Reproduire la logique de react.py
    lines = message.split('\n')
    chunks = []
    first_chunk = True
    for li, line in enumerate(lines):
        if li > 0:
            chunks.append("\n")
        if not line:
            continue
        words = line.split(' ')
        for wi in range(0, len(words), 4):
            chunk = " ".join(words[wi:wi + 4])
            if not first_chunk and wi > 0:
                chunk = " " + chunk
            chunks.append(chunk)
            first_chunk = False

    # Vérifier que la recomposition donne le message original
    reconstructed = "".join(chunks)
    assert reconstructed == message


def test_final_token_single_word():
    """Un message d'un seul mot produit un seul chunk."""
    message = "OK"
    lines = message.split('\n')
    chunks = []
    first_chunk = True
    for li, line in enumerate(lines):
        if li > 0:
            chunks.append("\n")
        if not line:
            continue
        words = line.split(' ')
        for wi in range(0, len(words), 4):
            chunk = " ".join(words[wi:wi + 4])
            if not first_chunk and wi > 0:
                chunk = " " + chunk
            chunks.append(chunk)
            first_chunk = False
    assert len(chunks) == 1
    assert chunks[0] == "OK"


def test_final_token_empty_message():
    """Un message vide ne produit aucun chunk."""
    message = ""
    lines = message.split('\n')
    chunks = []
    first_chunk = True
    for li, line in enumerate(lines):
        if li > 0:
            chunks.append("\n")
        if not line:
            continue
        words = line.split(' ')
        for wi in range(0, len(words), 4):
            chunk = " ".join(words[wi:wi + 4])
            if not first_chunk and wi > 0:
                chunk = " " + chunk
            chunks.append(chunk)
            first_chunk = False
    assert len(chunks) == 0


# ── Test 5: chat.py patterns ──

def test_capture_patterns_includes_final_token():
    """chat.py _CAPTURE_PATTERNS contient [FINAL_TOKEN]."""
    import inspect
    from web.routes import chat as chat_mod
    source = inspect.getsource(chat_mod)
    assert '"[FINAL_TOKEN]"' in source or "'[FINAL_TOKEN]'" in source


def test_chat_token_handler_emits_token_type():
    """chat.py a un handler qui émet type='token'."""
    import inspect
    from web.routes import chat as chat_mod
    source = inspect.getsource(chat_mod)
    assert '"type": "token"' in source or "'type': 'token'" in source


# ── Test 7: chat.js ──

def test_chat_js_has_token_handler():
    """chat.js contient le handler pour type==='token'."""
    chat_js = Path(__file__).parent.parent / "web" / "static" / "js" / "chat.js"
    content = chat_js.read_text(encoding="utf-8")
    assert "data.type==='token'" in content


def test_chat_js_streaming_cleanup_on_done():
    """chat.js nettoie le streaming msg quand done arrive."""
    chat_js = Path(__file__).parent.parent / "web" / "static" / "js" / "chat.js"
    content = chat_js.read_text(encoding="utf-8")
    assert "streaming-msg" in content or "_streamingMsgEl" in content
    # Vérifier que le done handler a du cleanup
    assert "window._streamingMsgEl=null" in content
