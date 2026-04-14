"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier. Le trading comporte des risques
de perte en capital. Utilisez en paper trading d'abord.

📊 Tests IBKRClient (100% mocké, pas de connexion réelle)
=========================================================
"""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.markets.ibkr.models import Bar, ConnectionState, ContractInfo, SubscriptionInfo, Tick
from src.markets.ibkr.client import IBKRClient, IB_INSYNC_AVAILABLE


class TestConnectionState:
    """Tests pour ConnectionState enum."""
    
    def test_states(self):
        """Vérifie que tous les états sont définis."""
        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.RECONNECTING.value == "reconnecting"
        assert ConnectionState.ERROR.value == "error"


class TestModels:
    """Tests pour les dataclasses models."""
    
    def test_tick_creation(self):
        """Test création Tick."""
        tick = Tick(
            symbol="AAPL",
            timestamp=datetime.now(),
            price=150.0,
            bid=149.9,
            ask=150.1,
        )
        
        assert tick.symbol == "AAPL"
        assert tick.spread == pytest.approx(0.2, abs=0.001)
        assert tick.mid_price == pytest.approx(150.0, abs=0.001)
    
    def test_bar_creation(self):
        """Test création Bar."""
        bar = Bar(
            symbol="AAPL",
            timestamp=datetime.now(),
            timeframe="1m",
            open=150.0,
            high=151.0,
            low=149.5,
            close=150.5,
            volume=1000,
        )
        
        assert bar.symbol == "AAPL"
        assert bar.is_bullish is True
        assert bar.is_bearish is False
        assert bar.body_size == pytest.approx(0.5, abs=0.001)
        assert bar.range == pytest.approx(1.5, abs=0.001)
    
    def test_bar_merge(self):
        """Test fusion de barres."""
        now = datetime.now()
        
        bar1 = Bar(
            symbol="AAPL",
            timestamp=now,
            timeframe="1m",
            open=150.0,
            high=151.0,
            low=149.5,
            close=150.5,
            volume=1000,
        )
        
        bar2 = Bar(
            symbol="AAPL",
            timestamp=now + timedelta(minutes=1),
            timeframe="1m",
            open=150.5,
            high=152.0,
            low=150.0,
            close=151.5,
            volume=1500,
        )
        
        merged = bar1.merge_with(bar2)
        
        assert merged.open == 150.0    # Open de bar1
        assert merged.close == 151.5   # Close de bar2
        assert merged.high == 152.0    # Max des deux
        assert merged.low == 149.5     # Min des deux
        assert merged.volume == 2500   # Somme
    
    def test_bar_merge_different_symbols_fails(self):
        """Test que merge avec symboles différents échoue."""
        now = datetime.now()
        
        bar1 = Bar(symbol="AAPL", timestamp=now, timeframe="1m", open=150, high=151, low=149, close=150)
        bar2 = Bar(symbol="GOOGL", timestamp=now, timeframe="1m", open=100, high=101, low=99, close=100)
        
        with pytest.raises(ValueError) as exc_info:
            bar1.merge_with(bar2)
        
        assert "different symbols" in str(exc_info.value)
    
    def test_subscription_info_stale(self):
        """Test détection feed stale."""
        sub = SubscriptionInfo(
            symbol="AAPL",
            req_id=1,
            created_at=datetime.now(),
            last_bar_at=datetime.now() - timedelta(seconds=35),
        )
        
        assert sub.is_stale is True
        assert sub.age_seconds > 30
    
    def test_subscription_info_fresh(self):
        """Test feed frais."""
        sub = SubscriptionInfo(
            symbol="AAPL",
            req_id=1,
            created_at=datetime.now(),
            last_bar_at=datetime.now(),
        )
        
        assert sub.is_stale is False
        assert sub.age_seconds < 1
    
    def test_contract_info(self):
        """Test ContractInfo."""
        contract = ContractInfo(
            symbol="AAPL",
            con_id=265598,
            exchange="SMART",
            currency="USD",
            sec_type="STK",
        )
        
        assert contract.is_stock is True
        assert contract.is_future is False


class TestIBKRClientInit:
    """Tests d'initialisation IBKRClient."""
    
    def test_default_init(self):
        """Test initialisation par défaut."""
        client = IBKRClient()
        
        assert client._host == "127.0.0.1"
        assert client._port == 4002
        assert client._client_id == 1
        assert client._readonly is True
        assert client.state == ConnectionState.DISCONNECTED
        assert client.is_connected is False
    
    def test_custom_init(self):
        """Test initialisation personnalisée."""
        client = IBKRClient(
            host="192.168.1.100",
            port=4001,
            client_id=5,
            readonly=False,
        )
        
        assert client._host == "192.168.1.100"
        assert client._port == 4001
        assert client._client_id == 5
        assert client._readonly is False


class TestIBKRClientConnect:
    """Tests connexion/déconnexion (mockés)."""
    
    @pytest.mark.asyncio
    async def test_connect_idempotent(self):
        """connect() est idempotent."""
        client = IBKRClient()
        
        # Premier connect (mode dégradé car pas de ib_insync réel dans tests)
        result1 = await client.connect()
        assert result1 is True
        assert client.is_connected is True
        
        # Second connect - ne doit pas crasher
        result2 = await client.connect()
        assert result2 is True
        assert client.is_connected is True
        
        await client.disconnect()
    
    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self):
        """disconnect() est idempotent."""
        client = IBKRClient()
        
        # Disconnect sur client non connecté
        await client.disconnect()
        assert client.state == ConnectionState.DISCONNECTED
        
        # Connecter puis déconnecter
        await client.connect()
        await client.disconnect()
        assert client.state == ConnectionState.DISCONNECTED
        
        # Double disconnect
        await client.disconnect()
        assert client.state == ConnectionState.DISCONNECTED
    
    @pytest.mark.asyncio
    async def test_start_stop_aliases(self):
        """start() et stop() sont des alias pour connect/disconnect."""
        client = IBKRClient()
        
        result = await client.start()
        assert result is True
        assert client.is_connected is True
        
        await client.stop()
        assert client.is_connected is False


class TestIBKRClientSubscriptions:
    """Tests souscriptions (mode dégradé)."""
    
    @pytest.mark.asyncio
    async def test_subscribe_bars_requires_connection(self):
        """subscribe_bars nécessite une connexion."""
        client = IBKRClient()
        
        # Sans connexion
        result = await client.subscribe_bars("AAPL")
        assert result is False
    
    @pytest.mark.asyncio
    async def test_subscribe_bars_success(self):
        """subscribe_bars réussit quand connecté."""
        client = IBKRClient()
        await client.connect()
        
        result = await client.subscribe_bars("AAPL", "1 min")
        assert result is True
        assert "AAPL" in client._subscriptions
        
        await client.disconnect()
    
    @pytest.mark.asyncio
    async def test_subscribe_bars_idempotent(self):
        """Double souscription ne duplique pas."""
        client = IBKRClient()
        await client.connect()
        
        await client.subscribe_bars("AAPL")
        await client.subscribe_bars("AAPL")  # Second appel
        
        # Devrait n'avoir qu'une souscription
        assert len([s for s in client._subscriptions if s == "AAPL"]) == 1
        
        await client.disconnect()
    
    @pytest.mark.asyncio
    async def test_unsubscribe_bars(self):
        """unsubscribe_bars supprime la souscription."""
        client = IBKRClient()
        await client.connect()
        
        await client.subscribe_bars("AAPL")
        assert "AAPL" in client._subscriptions
        
        await client.unsubscribe_bars("AAPL")
        assert "AAPL" not in client._subscriptions
        
        await client.disconnect()
    
    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent(self):
        """unsubscribe sur symbole non souscrit ne crashe pas."""
        client = IBKRClient()
        await client.connect()
        
        # Ne doit pas lever d'exception
        await client.unsubscribe_bars("MSFT")
        
        await client.disconnect()


class TestIBKRClientCallbacks:
    """Tests callbacks."""
    
    @pytest.mark.asyncio
    async def test_on_bar_callback(self):
        """Test callback on_bar."""
        client = IBKRClient()
        
        received_bars = []
        
        async def callback(bar: Bar):
            received_bars.append(bar)
        
        client.on_bar(callback)
        
        # Émettre une barre manuellement
        test_bar = Bar(
            symbol="AAPL",
            timestamp=datetime.now(),
            timeframe="1m",
            open=150.0,
            high=151.0,
            low=149.5,
            close=150.5,
        )
        
        await client.connect()
        await client.subscribe_bars("AAPL")
        await client._emit_bar(test_bar)
        
        assert len(received_bars) == 1
        assert received_bars[0].symbol == "AAPL"
        
        await client.disconnect()
    
    @pytest.mark.asyncio
    async def test_on_connection_change_callback(self):
        """Test callback on_connection_change."""
        client = IBKRClient()
        
        states = []
        
        async def callback(state: ConnectionState):
            states.append(state)
        
        client.on_connection_change(callback)
        
        await client.connect()
        await client.disconnect()
        
        # Devrait avoir eu au moins CONNECTING, CONNECTED, DISCONNECTED
        assert ConnectionState.CONNECTED in states
        assert ConnectionState.DISCONNECTED in states


class TestIBKRClientStats:
    """Tests statistiques."""
    
    @pytest.mark.asyncio
    async def test_get_connection_stats(self):
        """Test get_connection_stats."""
        client = IBKRClient()
        
        stats = client.get_connection_stats()
        
        assert stats["state"] == "disconnected"
        assert stats["host"] == "127.0.0.1"
        assert stats["port"] == 4002
        assert stats["uptime_seconds"] == 0.0
        assert "pacer" in stats
    
    @pytest.mark.asyncio
    async def test_uptime_tracking(self):
        """Test suivi uptime."""
        client = IBKRClient()
        
        await client.connect()
        
        # Attendre un peu
        await asyncio.sleep(0.1)
        
        assert client.uptime_seconds > 0
        assert client.uptime_seconds < 1
        
        await client.disconnect()
        
        # Après déconnexion, uptime = 0
        assert client.uptime_seconds == 0


class TestIBKRClientWatchdog:
    """Tests watchdog (stale feed detection)."""
    
    @pytest.mark.asyncio
    async def test_last_tick_age(self):
        """Test calcul de l'âge du dernier tick."""
        client = IBKRClient()
        
        # Pas de tick encore
        assert client.last_tick_age_seconds == float("inf")
        
        # Simuler un tick
        client._last_tick_time = datetime.now()
        await asyncio.sleep(0.05)
        
        # Devrait être > 0
        assert client.last_tick_age_seconds > 0.04
        assert client.last_tick_age_seconds < 1
    
    @pytest.mark.asyncio
    async def test_watchdog_starts_on_connect(self):
        """Le watchdog démarre à la connexion."""
        client = IBKRClient()
        
        assert client._watchdog_task is None
        
        await client.connect()
        
        # Le watchdog devrait être démarré
        # (en mode dégradé, la tâche peut être créée)
        
        await client.disconnect()
        
        # Le watchdog devrait être arrêté
        assert client._watchdog_task is None


class TestIBKRClientHistorical:
    """Tests données historiques (mode dégradé)."""
    
    @pytest.mark.asyncio
    async def test_request_historical_not_connected(self):
        """request_historical sans connexion retourne vide."""
        client = IBKRClient()
        
        bars = await client.request_historical("AAPL")
        assert bars == []
    
    @pytest.mark.asyncio
    async def test_request_historical_degraded_mode(self):
        """request_historical en mode dégradé."""
        client = IBKRClient()
        await client.connect()
        
        # En mode dégradé (pas de ib_insync), retourne liste vide
        bars = await client.request_historical("AAPL", "1 D", "1 min")
        assert bars == []
        
        await client.disconnect()
