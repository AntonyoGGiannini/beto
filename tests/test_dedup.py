"""Dedup de oportunidades no SQLite (não re-alertar a mesma surebet a cada ciclo)."""

from __future__ import annotations

from beto.arbitrage.engine import find_arbitrage
from beto.matching.matcher import EventMatcher
from beto.models import MarketType, Outcome
from beto.scrapers.base import ScrapeResult
from beto.storage.repository import OpportunityRepo
from helpers import make_quote


def _opportunity():
    events = EventMatcher().group(
        [
            make_quote(
                "casa_a",
                outcomes=[Outcome("home", 2.10), Outcome("draw", 3.10), Outcome("away", 3.50)],
            ),
            make_quote(
                "casa_b",
                outcomes=[Outcome("home", 1.95), Outcome("draw", 3.60), Outcome("away", 4.20)],
            ),
        ]
    )
    opp = find_arbitrage(events[0], MarketType.MATCH_1X2, None, bankroll=1000.0)
    assert opp is not None
    return opp


def test_seen_recently_com_ttl(tmp_path):
    repo = OpportunityRepo(str(tmp_path / "t.db"))
    opp = _opportunity()
    key = opp.dedup_key()

    assert not repo.seen_recently(key, ttl_s=3600)
    repo.mark_seen(opp)
    assert repo.seen_recently(key, ttl_s=3600)  # dentro do TTL → não re-alertar
    assert not repo.seen_recently(key, ttl_s=0)  # TTL expirado → pode alertar de novo
    assert repo.opportunities_today() == 1
    repo.close()


def test_record_runs_persiste_historico(tmp_path):
    repo = OpportunityRepo(str(tmp_path / "t.db"))
    repo.record_runs(
        [
            ScrapeResult("mock", [], ok=True, duration_s=0.1),
            ScrapeResult("betano", [], ok=False, error="HTTP 403", duration_s=1.2),
        ]
    )
    rows = repo._conn.execute("SELECT house, ok, error FROM scrape_runs").fetchall()
    assert ("mock", 1, None) in rows
    assert ("betano", 0, "HTTP 403") in rows
    repo.close()


def test_dedup_key_muda_se_odds_mudam_materialmente():
    opp = _opportunity()
    assert opp.dedup_key() == _opportunity().dedup_key()  # determinística
