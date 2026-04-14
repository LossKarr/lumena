"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier.

📊 Tests Grok JSON Repair
=========================
"""

import pytest

from src.markets.grok.client import GrokClient


class TestJsonRepair:
    """Tests pour la fonction _repair_json du GrokClient."""
    
    @pytest.fixture
    def client(self):
        """Crée un client Grok pour les tests."""
        return GrokClient(api_key="test-key")
    
    # ========================================================================
    # Tests JSON valide (pas de repair nécessaire)
    # ========================================================================
    
    def test_valid_json(self, client):
        """JSON valide ne nécessite pas de repair."""
        raw = '{"signals": [], "market_sentiment": "neutral"}'
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["market_sentiment"] == "neutral"
    
    def test_valid_json_with_whitespace(self, client):
        """JSON valide avec whitespace."""
        raw = """
        {
            "signals": [],
            "market_sentiment": "bullish"
        }
        """
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["market_sentiment"] == "bullish"
    
    # ========================================================================
    # Tests Markdown Wrapper
    # ========================================================================
    
    def test_remove_markdown_json_wrapper(self, client):
        """Retire le wrapper ```json ... ```."""
        raw = """```json
{
    "signals": [],
    "market_sentiment": "bearish"
}
```"""
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["market_sentiment"] == "bearish"
    
    def test_remove_markdown_wrapper_no_lang(self, client):
        """Retire le wrapper ``` ... ``` sans lang."""
        raw = """```
{"signals": [], "market_sentiment": "neutral"}
```"""
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["signals"] == []
    
    def test_markdown_with_extra_text(self, client):
        """Markdown avec texte avant/après."""
        raw = """Here is the JSON response:

```json
{"signals": [], "market_sentiment": "bullish"}
```

Hope this helps!"""
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["market_sentiment"] == "bullish"
    
    # ========================================================================
    # Tests Trailing Comma
    # ========================================================================
    
    def test_remove_trailing_comma_object(self, client):
        """Retire les trailing commas avant }."""
        raw = '{"signals": [], "market_sentiment": "neutral",}'
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["market_sentiment"] == "neutral"
    
    def test_remove_trailing_comma_array(self, client):
        """Retire les trailing commas avant ]."""
        raw = '{"signals": ["a", "b",], "market_sentiment": "neutral"}'
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["signals"] == ["a", "b"]
    
    def test_multiple_trailing_commas(self, client):
        """Retire plusieurs trailing commas."""
        raw = '{"a": [1, 2,], "b": {"x": 1,},}'
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["a"] == [1, 2]
        assert result["b"]["x"] == 1
    
    # ========================================================================
    # Tests Extraction JSON
    # ========================================================================
    
    def test_extract_json_from_text(self, client):
        """Extrait le JSON d'un texte avec préfixe/suffixe."""
        raw = """Sure, here is the analysis:
        
{"signals": [], "market_sentiment": "mixed"}

Let me know if you need more details."""
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["market_sentiment"] == "mixed"
    
    def test_extract_nested_json(self, client):
        """Extrait un JSON imbriqué."""
        raw = """Response: {"outer": {"inner": "value"}}"""
        result = client._repair_json(raw)
        
        assert result is not None
        assert result["outer"]["inner"] == "value"
    
    # ========================================================================
    # Tests Cas Complexes
    # ========================================================================
    
    def test_combined_issues(self, client):
        """Répare plusieurs problèmes combinés."""
        raw = """Here's the result:
```json
{
    "signals": [
        {"symbol": "AAPL", "direction": "long",},
    ],
    "market_sentiment": "bullish",
}
```
Done!"""
        result = client._repair_json(raw)
        
        assert result is not None
        assert len(result["signals"]) == 1
        assert result["signals"][0]["symbol"] == "AAPL"
    
    def test_real_world_grok_response(self, client):
        """Simule une vraie réponse Grok avec problèmes."""
        raw = """Based on my analysis, here is my assessment:

```json
{
  "signals": [
    {
      "symbol": "AAPL",
      "direction": "long",
      "strength": "moderate",
      "confidence": 0.75,
      "reason": "RSI at 35 with positive momentum divergence"
    },
    {
      "symbol": "MSFT",
      "direction": "neutral",
      "strength": "weak",
      "confidence": 0.45,
      "reason": "Mixed signals, waiting for confirmation"
    },
  ],
  "market_sentiment": "cautiously bullish"
}
```

Note: These are analytical observations, not trading recommendations."""
        result = client._repair_json(raw)
        
        assert result is not None
        assert len(result["signals"]) == 2
        assert result["signals"][0]["confidence"] == 0.75
        assert result["market_sentiment"] == "cautiously bullish"
    
    # ========================================================================
    # Tests Échecs (JSON irréparable)
    # ========================================================================
    
    def test_completely_invalid_json(self, client):
        """JSON complètement invalide retourne None."""
        raw = "This is not JSON at all, just plain text."
        result = client._repair_json(raw)
        
        assert result is None
    
    def test_truncated_json(self, client):
        """JSON tronqué retourne None."""
        raw = '{"signals": [{"symbol": "AAPL"'
        result = client._repair_json(raw)
        
        assert result is None
    
    def test_malformed_structure(self, client):
        """Structure malformée retourne None."""
        raw = '{"signals" "missing colon"}'
        result = client._repair_json(raw)
        
        assert result is None


class TestParseJson:
    """Tests pour _parse_json qui appelle _repair_json."""
    
    @pytest.fixture
    def client(self):
        """Crée un client Grok pour les tests."""
        return GrokClient(api_key="test-key")
    
    def test_parse_valid_json(self, client):
        """Parse JSON valide directement."""
        raw = '{"key": "value"}'
        result = client._parse_json(raw)
        
        assert result == {"key": "value"}
    
    def test_parse_with_repair(self, client):
        """Parse avec repair si nécessaire."""
        raw = '```json\n{"key": "value"}\n```'
        result = client._parse_json(raw)
        
        assert result == {"key": "value"}
    
    def test_parse_irreparable(self, client):
        """Retourne None si irréparable."""
        raw = "Not JSON"
        result = client._parse_json(raw)
        
        assert result is None
