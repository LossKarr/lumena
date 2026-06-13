import time
from urllib.request import urlopen

from src.tools.website_builder import start_preview_server, stop_preview_server


def test_preview_server_serves_js_as_javascript(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><script src='main.js'></script>", encoding="utf-8")
    (tmp_path / "main.js").write_text("console.log('preview ok');", encoding="utf-8")

    server = start_preview_server(tmp_path, 8090)
    try:
        assert server.get("success"), server
        last_error = None
        content_type = ""
        for _ in range(20):
            try:
                with urlopen(f"{server['url']}/main.js", timeout=2) as response:
                    content_type = response.headers.get("Content-Type", "")
                break
            except Exception as exc:
                last_error = exc
                time.sleep(0.1)
        assert content_type, last_error
        assert "javascript" in content_type.lower()
        assert "application/json" not in content_type.lower()
    finally:
        stop_preview_server()
