"""
⚠️ DISCLAIMER: Ce module est un outil d'analyse technique automatisée.
Il ne constitue PAS un conseil financier. Le trading comporte des risques
de perte en capital. Utilisez en paper trading d'abord.

📊 Tests TokenBucket et IBKRPacer
=================================
"""

import asyncio
import time

import pytest

from src.markets.ibkr.rate_limiter import TokenBucket, IBKRPacer


class TestTokenBucket:
    """Tests pour TokenBucket."""
    
    def test_initial_state(self):
        """Le bucket démarre plein."""
        bucket = TokenBucket(rate=10.0, burst=20)
        assert bucket.available == 20
        assert bucket.is_full
        assert not bucket.is_empty
    
    def test_try_acquire_success(self):
        """try_acquire consomme des tokens."""
        bucket = TokenBucket(rate=10.0, burst=10)
        
        assert bucket.try_acquire(5) is True
        assert bucket.available == 5
    
    def test_try_acquire_fail_insufficient(self):
        """try_acquire échoue si pas assez de tokens."""
        bucket = TokenBucket(rate=10.0, burst=5)
        
        # Vider le bucket
        bucket.try_acquire(5)
        
        # Pas assez de tokens
        assert bucket.try_acquire(1) is False
        assert bucket.is_empty
    
    @pytest.mark.asyncio
    async def test_acquire_blocks_until_available(self):
        """acquire bloque jusqu'à ce que les tokens soient disponibles."""
        bucket = TokenBucket(rate=100.0, burst=5)  # 100 tokens/s = 10ms par token
        
        # Vider le bucket
        bucket.try_acquire(5)
        
        # Demander 1 token - devrait bloquer ~10ms
        start = time.monotonic()
        await bucket.acquire(1)
        elapsed = time.monotonic() - start
        
        # Doit avoir attendu environ 10ms (avec marge)
        assert elapsed >= 0.005  # Au moins 5ms
        assert elapsed < 0.5  # Pas trop longtemps
    
    @pytest.mark.asyncio
    async def test_acquire_multiple_sequential(self):
        """acquire multiple séquentiel."""
        bucket = TokenBucket(rate=50.0, burst=10)
        
        for _ in range(10):
            await bucket.acquire(1)
        
        # Bucket devrait être vide (ou presque)
        assert bucket.available <= 1
    
    def test_refill_over_time(self):
        """Le bucket se remplit avec le temps."""
        bucket = TokenBucket(rate=1000.0, burst=100)  # 1000/s
        
        # Consommer tout
        bucket.try_acquire(100)
        assert bucket.is_empty
        
        # Attendre 50ms = ~50 tokens
        time.sleep(0.05)
        
        # Vérifier que des tokens ont été ajoutés (avec large marge pour timing)
        assert bucket.available >= 35  # Au moins 35 tokens
        assert bucket.available <= 70  # Pas plus de 70


class TestIBKRPacer:
    """Tests pour IBKRPacer."""
    
    def test_initial_state(self):
        """État initial du pacer."""
        pacer = IBKRPacer()
        
        stats = pacer.get_stats()
        assert stats["total_messages"] == 0
        assert stats["total_historical"] == 0
        assert stats["pacing_violations"] == 0
        assert stats["in_cooldown"] is False
    
    @pytest.mark.asyncio
    async def test_acquire_message(self):
        """acquire_message incrémente le compteur."""
        pacer = IBKRPacer()
        
        await pacer.acquire_message(1)
        await pacer.acquire_message(2)
        
        stats = pacer.get_stats()
        assert stats["total_messages"] == 3
    
    @pytest.mark.asyncio
    async def test_acquire_historical(self):
        """acquire_historical incrémente le compteur."""
        pacer = IBKRPacer()
        
        await pacer.acquire_historical()
        await pacer.acquire_historical()
        
        stats = pacer.get_stats()
        assert stats["total_historical"] == 2
    
    def test_report_pacing_violation_activates_cooldown(self):
        """report_pacing_violation active le cooldown."""
        pacer = IBKRPacer()
        
        assert pacer.is_in_cooldown is False
        
        pacer.report_pacing_violation()
        
        assert pacer.is_in_cooldown is True
        assert pacer.cooldown_remaining > 0
        
        stats = pacer.get_stats()
        assert stats["pacing_violations"] == 1
    
    def test_multiple_pacing_violations(self):
        """Plusieurs violations incrémentent le compteur."""
        pacer = IBKRPacer()
        
        pacer.report_pacing_violation()
        pacer.report_pacing_violation()
        pacer.report_pacing_violation()
        
        stats = pacer.get_stats()
        assert stats["pacing_violations"] == 3
    
    @pytest.mark.asyncio
    async def test_acquire_waits_for_cooldown(self):
        """acquire attend la fin du cooldown."""
        pacer = IBKRPacer()
        
        # Réduire le cooldown pour le test
        pacer.PACING_VIOLATION_COOLDOWN = 0.1
        pacer.report_pacing_violation()
        
        start = time.monotonic()
        await pacer.acquire_message()
        elapsed = time.monotonic() - start
        
        # Devrait avoir attendu environ 100ms
        assert elapsed >= 0.08  # Avec petite marge


class TestTokenBucketStress:
    """Tests de stress pour TokenBucket."""
    
    @pytest.mark.asyncio
    async def test_concurrent_acquire(self):
        """Plusieurs tâches concurrent accédant au bucket."""
        bucket = TokenBucket(rate=100.0, burst=50)
        
        async def consumer(n: int) -> int:
            """Consomme n tokens."""
            await bucket.acquire(n)
            return n
        
        # Lancer plusieurs consommateurs
        tasks = [
            asyncio.create_task(consumer(5)),
            asyncio.create_task(consumer(5)),
            asyncio.create_task(consumer(5)),
            asyncio.create_task(consumer(5)),
            asyncio.create_task(consumer(5)),
        ]
        
        results = await asyncio.gather(*tasks)
        
        # Tous doivent avoir réussi
        assert sum(results) == 25
    
    @pytest.mark.asyncio
    async def test_respects_rate_limit(self):
        """Vérifie que le rate limit est respecté."""
        rate = 50.0  # 50 tokens/s
        bucket = TokenBucket(rate=rate, burst=10)
        
        # Consommer le burst
        bucket.try_acquire(10)
        
        # Mesurer le temps pour 10 tokens supplémentaires
        start = time.monotonic()
        for _ in range(10):
            await bucket.acquire(1)
        elapsed = time.monotonic() - start
        
        # Devrait prendre environ 10/50 = 0.2s
        assert elapsed >= 0.15  # Au moins 150ms
        assert elapsed < 1.0    # Pas plus d'1s
