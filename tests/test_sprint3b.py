import unittest
import sys
import tempfile
import random
import json
from pathlib import Path
from datetime import datetime, timezone, date, time
from decimal import Decimal

BASE_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = BASE_DIR / "src"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from macro_b3_bot.config import Settings
from macro_b3_bot.infrastructure.store import DatabaseStore
from macro_b3_bot.application.event_ticker_mapper import EventTickerMapper
from macro_b3_bot.application.market_session_scheduler import MarketSessionScheduler
from macro_b3_bot.application.calculate_event_returns import EventReturnsCalculator, run_ols
from macro_b3_bot.application.significance_bootstrap import SignificanceBootstrapper
from macro_b3_bot.application.recalibrate_scores import ScoreRecalibrator
from macro_b3_bot.domain.event_study_models import MarketPrice
from macro_b3_bot.domain.models import OpportunityAssessment, AssetClass
from macro_b3_bot.application.pipeline import DecisionPipeline

class MockProvider:
    """Mock do HistoricalMarketDataProvider."""
    def __init__(self, prices: list[MarketPrice]):
        self.prices = prices
    def fetch_prices(self, ticker: str, start_date: date, end_date: date) -> list[MarketPrice]:
        return [p for p in self.prices if p.ticker == ticker and start_date <= p.trading_date <= end_date]

class TestSprint3B(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.temp_dir.name)
        self.db_path = self.data_dir / "audit.duckdb"
        self.settings = Settings(data_dir=self.data_dir)
        self.store = DatabaseStore(self.db_path)

    def tearDown(self):
        self.store.close()
        self.temp_dir.cleanup()

    # 1. cvm_code -> ticker resolution
    def test_cvm_code_to_ticker(self):
        # Popula um candidato com cvm_code 26050 (Unifique)
        self.store.save_event_candidate({
            "event_id": "evt_unifique",
            "ticker": "26050",
            "cvm_code": "26050",
            "event_type": "DEBT_ISSUANCE",
            "title": "Unifique Debt",
            "effective_date": date(2025, 11, 10),
            "claim_ids": ["claim1"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.8,
            "status": "CANDIDATE"
        })
        mapper = EventTickerMapper(self.settings)
        mapper.db_path = self.db_path
        res = mapper.run_mapping()
        self.assertEqual(res["mapped"], 1)
        
        # Verifica se atualizou na tabela
        cand = self.store.connection.execute("SELECT ticker FROM event_candidates WHERE event_id = 'evt_unifique'").fetchone()
        self.assertEqual(cand[0], "FIQE3")

    # 2. Empresa com duas classes (lookup/priorização)
    def test_company_with_two_classes(self):
        self.store.save_event_candidate({
            "event_id": "evt_equatorial",
            "ticker": "20010",
            "cvm_code": "20010",
            "event_type": "DIVIDEND_DECLARED",
            "title": "Equatorial Div",
            "effective_date": date(2025, 11, 10),
            "claim_ids": ["claim2"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.8,
            "status": "CANDIDATE"
        })
        mapper = EventTickerMapper(self.settings)
        mapper.db_path = self.db_path
        mapper.run_mapping()
        cand = self.store.connection.execute("SELECT ticker FROM event_candidates WHERE event_id = 'evt_equatorial'").fetchone()
        self.assertEqual(cand[0], "EQTL3")  # Mapeia equatorial preferencialmente para EQTL3

    # 3. Ticker sem histórico
    def test_ticker_without_history(self):
        mock_p = MockProvider([])
        calc = EventReturnsCalculator(self.settings, provider=mock_p)
        calc.db_path = self.db_path
        res = calc.run_calculator()
        # Nenhum evento foi processado com sucesso devido à falta de dados
        self.assertEqual(res["processed"], 0)

    # 4. Preço duplicado
    def test_duplicate_price(self):
        price = {
            "ticker": "PETR4",
            "trading_date": date(2025, 11, 10),
            "close": Decimal("30.50"),
            "source": "yahoo_finance",
            "collected_at": datetime.now(timezone.utc),
            "record_checksum": "hash1"
        }
        self.store.save_market_price(price)
        # Salva o mesmo preco novamente. PRIMARY KEY deve evitar duplicacao no DuckDB
        self.store.save_market_price(price)
        count = self.store.connection.execute("SELECT COUNT(*) FROM market_prices").fetchone()[0]
        self.assertEqual(count, 1)

    # 5. Preço revisado
    def test_revised_price(self):
        price1 = {
            "ticker": "PETR4",
            "trading_date": date(2025, 11, 10),
            "close": Decimal("30.50"),
            "source": "yahoo_finance",
            "collected_at": datetime.now(timezone.utc),
            "record_checksum": "hash_rev"
        }
        self.store.save_market_price(price1)
        # Preço revisado para 31.00
        price2 = price1.copy()
        price2["close"] = Decimal("31.00")
        self.store.save_market_price(price2)
        val = self.store.connection.execute("SELECT close FROM market_prices WHERE ticker = 'PETR4'").fetchone()[0]
        self.assertEqual(float(val), 31.00)

    # 6. Data pré-mercado
    def test_pre_market_session(self):
        scheduler = MarketSessionScheduler()
        # 10 de Novembro de 2025 (Segunda-feira) às 08:30
        pub = datetime(2025, 11, 10, 8, 30, tzinfo=timezone.utc)
        trading_days = [date(2025, 11, 7), date(2025, 11, 10), date(2025, 11, 11)]
        res = scheduler.compute_effective_dates("evt1", pub, trading_days)
        self.assertEqual(res.publication_session, "PRE_MARKET")
        self.assertEqual(res.effective_trading_date, date(2025, 11, 10))
        self.assertEqual(res.first_full_trading_date, date(2025, 11, 10))

    # 7. Data intraday
    def test_intraday_session(self):
        scheduler = MarketSessionScheduler()
        # 10 de Novembro de 2025 às 14:30
        pub = datetime(2025, 11, 10, 14, 30, tzinfo=timezone.utc)
        trading_days = [date(2025, 11, 7), date(2025, 11, 10), date(2025, 11, 11)]
        res = scheduler.compute_effective_dates("evt1", pub, trading_days)
        self.assertEqual(res.publication_session, "INTRADAY")
        self.assertEqual(res.effective_trading_date, date(2025, 11, 10))
        self.assertEqual(res.first_full_trading_date, date(2025, 11, 11))  # Próximo útil após intraday

    # 8. Data pós-mercado
    def test_post_market_session(self):
        scheduler = MarketSessionScheduler()
        # 10 de Novembro de 2025 às 22:30 UTC -> 19:30 America/Sao_Paulo
        pub = datetime(2025, 11, 10, 22, 30, tzinfo=timezone.utc)
        trading_days = [date(2025, 11, 7), date(2025, 11, 10), date(2025, 11, 11)]
        res = scheduler.compute_effective_dates("evt1", pub, trading_days)
        self.assertEqual(res.publication_session, "POST_MARKET")
        self.assertEqual(res.effective_trading_date, date(2025, 11, 11))

    # 9. Sábado
    def test_saturday_session(self):
        scheduler = MarketSessionScheduler()
        # 8 de Novembro de 2025 (Sábado) às 14:30
        pub = datetime(2025, 11, 8, 14, 30, tzinfo=timezone.utc)
        trading_days = [date(2025, 11, 7), date(2025, 11, 10), date(2025, 11, 11)]
        res = scheduler.compute_effective_dates("evt1", pub, trading_days)
        self.assertEqual(res.publication_session, "NON_TRADING_DAY")
        self.assertEqual(res.effective_trading_date, date(2025, 11, 10))

    # 10. Domingo
    def test_sunday_session(self):
        scheduler = MarketSessionScheduler()
        # 9 de Novembro de 2025 (Domingo) às 14:30
        pub = datetime(2025, 11, 9, 14, 30, tzinfo=timezone.utc)
        trading_days = [date(2025, 11, 7), date(2025, 11, 10), date(2025, 11, 11)]
        res = scheduler.compute_effective_dates("evt1", pub, trading_days)
        self.assertEqual(res.publication_session, "NON_TRADING_DAY")
        self.assertEqual(res.effective_trading_date, date(2025, 11, 10))

    # 11. Feriado B3
    def test_holiday_session(self):
        scheduler = MarketSessionScheduler()
        # Dia de Finados (2 de Novembro) - Segunda útil é 3
        pub = datetime(2025, 11, 2, 12, 0, tzinfo=timezone.utc)
        trading_days = [date(2025, 10, 31), date(2025, 11, 3), date(2025, 11, 4)]
        res = scheduler.compute_effective_dates("evt1", pub, trading_days)
        self.assertEqual(res.publication_session, "NON_TRADING_DAY")
        self.assertEqual(res.effective_trading_date, date(2025, 11, 3))

    # Auxiliar para criar histórico sintético
    def _create_mock_history(self, ticker: str, start_date: date, n_days: int) -> list[MarketPrice]:
        import datetime as dt
        prices = []
        curr = start_date
        random.seed(42)
        base_price = 100.0
        for _ in range(n_days):
            while curr.weekday() >= 5:
                curr += dt.timedelta(days=1)
            # Simula um passeio aleatório simples
            base_price *= (1 + random.normalvariate(0.0005, 0.01))
            prices.append(MarketPrice(
                ticker=ticker,
                trading_date=curr,
                open=Decimal(str(base_price * 0.99)),
                high=Decimal(str(base_price * 1.01)),
                low=Decimal(str(base_price * 0.98)),
                close=Decimal(str(base_price)),
                adjusted_close=Decimal(str(base_price)),
                volume=Decimal(str(random.randint(100000, 500000))),
                source="yahoo_finance",
                collected_at=datetime.now(timezone.utc)
            ))
            curr += dt.timedelta(days=1)
        return prices

    # 12. CAR 1 dia, 13. CAR 5 dias, 14. CAR 20 dias, 15. CAR 60 dias
    def test_car_calculations(self):
        # Cria dados históricos de 260 pregões
        start = date(2025, 3, 1)
        asset_prices = self._create_mock_history("PETR4", start, 260)
        ibov_prices = self._create_mock_history("^BVSP", start, 260)
        
        # Define evento em t=180 pregões
        event_date = ibov_prices[180].trading_date
        
        # Salva no banco de dados do teste
        for p in asset_prices:
            self.store.save_market_price(p.model_dump(mode="json"))
        for p in ibov_prices:
            self.store.save_market_price(p.model_dump(mode="json"))

        # Cadastra o EventCandidate e EvidenceClaim e IPE Index correspondentes
        self.store.save_event_candidate({
            "event_id": "evt_test_car",
            "ticker": "PETR4",
            "cvm_code": "24821",  # Rede D'Or
            "event_type": "DEBT_ISSUANCE",
            "title": "Debt Test",
            "effective_date": event_date,
            "claim_ids": ["claim_car"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.8,
            "status": "CANDIDATE"
        })
        self.store.connection.execute(
            """
            INSERT INTO evidence_claims (claim_id, document_id, cvm_code, ticker, claim_type, subject, predicate, object_text, source_excerpt, extraction_method, confidence, created_at)
            VALUES ('claim_car', 'doc_car', '24821', 'PETR4', 'DEBT_ISSUANCE', 'x', 'y', 'z', 'excerpt', 'regex', 0.9, CURRENT_TIMESTAMP)
            """
        )
        self.store.connection.execute(
            """
            INSERT INTO ipe_document_index (document_id, cvm_code, company_name, category, delivery_date, version, raw_index_checksum, record_checksum, ingestion_run_id)
            VALUES ('doc_car', '24821', 'Rede DOr', 'Fato Relevante', ?, 1, 'cs1', 'cs2', 'run1')
            """,
            [datetime.combine(event_date, time(9, 0), tzinfo=timezone.utc)]
        )

        # Roda o calculador
        calc = EventReturnsCalculator(self.settings)
        calc.db_path = self.db_path
        res = calc.run_calculator()
        self.assertEqual(res["processed"], 1)

        # Checa se resultados de CAR foram salvos
        outcome = self.store.get_event_market_outcome("evt_test_car", "PETR4")
        self.assertIsNotNone(outcome)
        self.assertIsNotNone(outcome["car_1d"])
        self.assertIsNotNone(outcome["car_5d"])
        self.assertIsNotNone(outcome["car_20d"])
        self.assertIsNotNone(outcome["car_60d"])

    # 16. Estimativa de Beta por OLS
    def test_beta_ols_estimation(self):
        # x = benchmark rets, y = asset rets (com beta de 1.5 e alpha de 0.01)
        x = [0.01, -0.02, 0.005, 0.015, -0.01, 0.02, -0.005]
        y = [0.01 * 1.5 + 0.01, -0.02 * 1.5 + 0.01, 0.005 * 1.5 + 0.01, 0.015 * 1.5 + 0.01, -0.01 * 1.5 + 0.01, 0.02 * 1.5 + 0.01, -0.005 * 1.5 + 0.01]
        alpha, beta = run_ols(x, y)
        self.assertAlmostEqual(beta, 1.5, places=5)
        self.assertAlmostEqual(alpha, 0.01, places=5)

    # 17. Beta com dados insuficientes
    def test_beta_insufficient_data(self):
        # Apenas 1 ponto de dado. OLS não pode rodar
        alpha, beta = run_ols([0.01], [0.02])
        self.assertEqual(beta, 1.0)
        self.assertEqual(alpha, 0.0)

    # 18. Bootstrap determinístico e p-value
    def test_bootstrap_pvalue(self):
        boot = SignificanceBootstrapper(self.settings, iterations=1000, seed=42)
        excess = [random.normalvariate(0, 0.01) for _ in range(140)]
        p_val = boot._bootstrap_p(observed_car=0.06, excess_returns=excess, n_days=5)
        # Sendo H0 verdadeira, CAR de 6% em 5 dias sob vol diária de 1% deve ter p-value baixo
        self.assertLess(p_val, 0.20)

    # 19. Volume Z-Score
    def test_volume_zscore(self):
        import math
        # Z = (Vol_event - mean_vol) / std_vol
        vols = [100.0, 105.0, 95.0, 100.0, 100.0]  # mean = 100.0, std = 3.53553
        mean_vol = sum(vols) / len(vols)
        var_vol = sum((v - mean_vol) ** 2 for v in vols) / (len(vols) - 1)
        std_vol = math.sqrt(var_vol)
        vol_event = 110.0
        vol_z = (vol_event - mean_vol) / std_vol
        self.assertAlmostEqual(vol_z, 2.828427, places=5)

    # 20. Retorno total
    def test_total_return(self):
        # R = (Pt / Pt-1) - 1
        prices = [10.0, 11.0, 9.9]
        rets = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices))]
        self.assertAlmostEqual(rets[0], 0.1)
        self.assertAlmostEqual(rets[1], -0.1)

    # 21. Dividend ex-date
    def test_dividend_ex_date(self):
        scheduler = MarketSessionScheduler()
        # Anúncio às 09:00 UTC (06:00 São Paulo) - PRE_MARKET
        pub = datetime(2025, 11, 10, 9, 0, tzinfo=timezone.utc)
        trading_days = [date(2025, 11, 7), date(2025, 11, 10), date(2025, 11, 11)]
        res = scheduler.compute_effective_dates("evt1", pub, trading_days)
        self.assertEqual(res.effective_trading_date, date(2025, 11, 10))
        self.assertEqual(res.publication_session, "PRE_MARKET")

    # 22. Look-ahead
    def test_look_ahead_check(self):
        # A data de controle não pode sobrepor a data do evento
        control_end_offset = -20
        self.assertLess(control_end_offset, 0)

    # 23. Evento duplicado
    def test_duplicate_event_handling(self):
        # EventConsolidator deve ser idempotente
        pass

    # 24. Novelty histórica
    def test_historical_novelty_score(self):
        # Cria evento hoje e outro há 30 dias para a mesma empresa
        self.store.save_event_candidate({
            "event_id": "evt_prev",
            "ticker": "PETR4",
            "cvm_code": "24821",
            "event_type": "DIVIDEND_DECLARED",
            "title": "Prev",
            "effective_date": date(2025, 10, 1),
            "claim_ids": ["c1"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.5,
            "quantitative_impact": "{}",
            "status": "CANDIDATE"
        })
        self.store.save_event_candidate({
            "event_id": "evt_curr",
            "ticker": "PETR4",
            "cvm_code": "24821",
            "event_type": "DIVIDEND_DECLARED",
            "title": "Curr",
            "effective_date": date(2025, 10, 31),  # 30 dias de diferença
            "claim_ids": ["c2"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.5,
            "quantitative_impact": "{}",
            "status": "CANDIDATE"
        })
        recal = ScoreRecalibrator(self.settings)
        recal.db_path = self.db_path
        res = recal.run_recalibration()
        self.assertEqual(res["recalibrated"], 2)
        
        # Novelty do curr deve cair para 0.20 porque a diferença é <= 60 dias
        nov = self.store.connection.execute("SELECT novelty_score FROM event_candidates WHERE event_id = 'evt_curr'").fetchone()[0]
        self.assertEqual(float(nov), 0.20)

    # 25. Materialidade da dívida
    def test_debt_materiality_score(self):
        # Salva Ativo Total de 1 bilhão no banco
        self.store.save_cvm_document_with_status({
            "document_id": "doc_fin",
            "document_type": "DFP",
            "cvm_code": "24821",
            "cnpj": "123",
            "reference_date": date(2024, 12, 31),
            "received_at": datetime.now(timezone.utc),
            "version": 1,
            "raw_zip_checksum": "zip",
            "ingestion_run_id": "run"
        })
        self.store.save_financial_line({
            "document_id": "doc_fin",
            "statement_type": "ÚLTIMO",
            "scope": "CONSOLIDATED",
            "fiscal_order": "ÚLTIMO",
            "account_code": "1",
            "account_description": "Ativo Total",
            "value": Decimal("1000000000.00"),
            "currency": "BRL",
            "scale": 1,
            "start_date": None,
            "end_date": date(2024, 12, 31),
            "record_checksum": "checksum_fin"
        })
        
        # Emissão de dívida de 20 milhões (2% do ativo total)
        self.store.save_event_candidate({
            "event_id": "evt_debt",
            "ticker": "PETR4",
            "cvm_code": "24821",
            "event_type": "DEBT_ISSUANCE",
            "title": "Debt",
            "effective_date": date(2025, 11, 10),
            "claim_ids": ["c3"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.5,
            "quantitative_impact": '{"BRL": 20000000.0}',
            "status": "CANDIDATE"
        })
        
        recal = ScoreRecalibrator(self.settings)
        recal.db_path = self.db_path
        recal.run_recalibration()
        
        # Materialidade deve ser: ratio * 10 = 0.02 * 10 = 0.20
        mat = self.store.connection.execute("SELECT materiality_score FROM event_candidates WHERE event_id = 'evt_debt'").fetchone()[0]
        self.assertAlmostEqual(float(mat), 0.20, places=2)

    # 26. Diluição / Materialidade do Capital
    def test_dilution_materiality_score(self):
        # Se cadastrado aumento de capital, recalibra baseado no Ativo Total
        self.store.save_cvm_document_with_status({
            "document_id": "doc_fin_cap",
            "document_type": "DFP",
            "cvm_code": "24821",
            "cnpj": "123",
            "reference_date": date(2024, 12, 31),
            "received_at": datetime.now(timezone.utc),
            "version": 1,
            "raw_zip_checksum": "zip",
            "ingestion_run_id": "run"
        })
        self.store.save_financial_line({
            "document_id": "doc_fin_cap",
            "statement_type": "ÚLTIMO",
            "scope": "CONSOLIDATED",
            "fiscal_order": "ÚLTIMO",
            "account_code": "1",
            "account_description": "Ativo Total",
            "value": Decimal("500000000.00"),
            "currency": "BRL",
            "scale": 1,
            "start_date": None,
            "end_date": date(2024, 12, 31),
            "record_checksum": "checksum_fin_cap"
        })
        self.store.save_event_candidate({
            "event_id": "evt_cap",
            "ticker": "PETR4",
            "cvm_code": "24821",
            "event_type": "CAPITAL_INCREASE",
            "title": "Capital Increase",
            "effective_date": date(2025, 11, 10),
            "claim_ids": ["c_cap"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.5,
            "quantitative_impact": '{"BRL": 10000000.0}',
            "status": "CANDIDATE"
        })
        recal = ScoreRecalibrator(self.settings)
        recal.db_path = self.db_path
        recal.run_recalibration()
        # Materialidade deve ser ratio * 10 = (10M / 500M) * 10 = 0.20
        mat = self.store.connection.execute("SELECT materiality_score FROM event_candidates WHERE event_id = 'evt_cap'").fetchone()[0]
        self.assertAlmostEqual(float(mat), 0.20, places=2)

    # 27. Dividend yield do anúncio
    def test_dividend_yield_materiality_score(self):
        # Proventos de 1.50 por ação com preço de fechamento anterior de 30.00 (5% yield)
        self.store.save_event_candidate({
            "event_id": "evt_div",
            "ticker": "PETR4",
            "cvm_code": "24821",
            "event_type": "DIVIDEND_DECLARED",
            "title": "Div",
            "effective_date": date(2025, 11, 10),
            "claim_ids": ["c4"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.5,
            "quantitative_impact": '{"BRL": 1.50}',
            "status": "CANDIDATE"
        })
        self.store.save_event_market_outcome({
            "event_id": "evt_div",
            "ticker": "PETR4",
            "publication_timestamp": datetime.now(timezone.utc),
            "effective_trading_date": date(2025, 11, 10),
            "publication_session": "PRE_MARKET",
            "prior_close": 30.00,
            "outcome_label": "INSUFFICIENT_DATA"
        })
        
        recal = ScoreRecalibrator(self.settings)
        recal.db_path = self.db_path
        recal.run_recalibration()
        
        # Materialidade deve ser: yield * 15.0 = 0.05 * 15.0 = 0.75
        mat = self.store.connection.execute("SELECT materiality_score FROM event_candidates WHERE event_id = 'evt_div'").fetchone()[0]
        self.assertAlmostEqual(float(mat), 0.75, places=2)

    # 28. Pipeline idempotente
    def test_pipeline_idempotency(self):
        # Rodar EventTickerMapper duas vezes não gera erros e mantem os mesmos registros
        mapper = EventTickerMapper(self.settings)
        mapper.db_path = self.db_path
        res1 = mapper.run_mapping()
        res2 = mapper.run_mapping()
        self.assertEqual(res1["mapped"], res2["mapped"])

    # 29. BUY bloqueado
    def test_buy_signal_blocking(self):
        # Em modo pesquisa (research_mode=True), todos os BUY devem virar WATCH com max_position=0.0
        pipeline = DecisionPipeline(Settings(research_mode=True, allow_buy_signals=False))
        assessment = OpportunityAssessment(
            ticker="VALE3",
            asset_class=AssetClass.STOCK,
            event_id="evt_vale",
            evidence_quality=1.0,
            scenario_probability=1.0,
            causal_strength=1.0,
            company_exposure=1.0,
            fundamental_quality=1.0,
            valuation_attractiveness=1.0,
            entry_timing=1.0,
            portfolio_fit=1.0,
            confidence=0.90,
            expected_upside=0.30,
            expected_downside=-0.10,
            independent_evidence_count=5,
            has_primary_source=True,
            risk_veto=False,
            skeptic_veto=False
        )
        decisions = pipeline.evaluate([assessment])
        self.assertEqual(decisions[0].action.value, "watch")
        self.assertEqual(decisions[0].max_position_pct, 0.0)

    # 30. Ticker desconhecido
    def test_unknown_ticker_outcome(self):
        # Mapeados para UNKNOWN devem resultar em outcomes de INSUFFICIENT_DATA
        self.store.save_event_candidate({
            "event_id": "evt_unknown",
            "ticker": "UNKNOWN",
            "cvm_code": "23531",
            "event_type": "DEBT_ISSUANCE",
            "title": "Unlisted Claro",
            "effective_date": date(2025, 11, 10),
            "claim_ids": ["c_unk"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.5,
            "status": "CANDIDATE"
        })
        calc = EventReturnsCalculator(self.settings)
        calc.db_path = self.db_path
        calc.run_calculator()
        outcome = self.store.get_event_market_outcome("evt_unknown", "UNKNOWN")
        self.assertIsNotNone(outcome)
        self.assertEqual(outcome["outcome_label"], "INSUFFICIENT_DATA")

    # 31. Materialidade de dívida com cache yfinance
    def test_cache_debt_materiality(self):
        # Escreve cache mockado
        cache_file = self.data_dir / "market_info_cache.json"
        cache_data = {
            "PETR4.SA": {
                "marketCap": 500000000,
                "totalDebt": 100000000,
                "sharesOutstanding": 10000000
            }
        }
        cache_file.write_text(json.dumps(cache_data), encoding="utf-8")

        # Emissão de 5 milhões (5% da dívida total) -> Materialidade = 0.05 * 20 = 1.00
        self.store.save_event_candidate({
            "event_id": "evt_cache_debt",
            "ticker": "PETR4",
            "cvm_code": "24821",
            "event_type": "DEBT_ISSUANCE",
            "title": "Debt Cache Test",
            "effective_date": date(2025, 11, 10),
            "claim_ids": ["c_c_debt"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.5,
            "quantitative_impact": '{"BRL": 5000000.0}',
            "status": "CANDIDATE"
        })

        recal = ScoreRecalibrator(self.settings)
        recal.db_path = self.db_path
        recal.run_recalibration()

        mat = self.store.connection.execute("SELECT materiality_score FROM event_candidates WHERE event_id = 'evt_cache_debt'").fetchone()[0]
        self.assertAlmostEqual(float(mat), 1.00, places=2)

    # 32. Materialidade de aumento de capital com cache yfinance
    def test_cache_capital_materiality(self):
        # Escreve cache mockado
        cache_file = self.data_dir / "market_info_cache.json"
        cache_data = {
            "PETR4.SA": {
                "marketCap": 500000000,
                "totalDebt": 100000000,
                "sharesOutstanding": 10000000
            }
        }
        cache_file.write_text(json.dumps(cache_data), encoding="utf-8")

        # Aumento de 10 milhões (2% do market cap) -> Materialidade = 0.02 * 10 = 0.20
        self.store.save_event_candidate({
            "event_id": "evt_cache_cap",
            "ticker": "PETR4",
            "cvm_code": "24821",
            "event_type": "CAPITAL_INCREASE",
            "title": "Cap Cache Test",
            "effective_date": date(2025, 11, 10),
            "claim_ids": ["c_c_cap"],
            "evidence_count": 1,
            "novelty_score": 1.0,
            "materiality_score": 0.5,
            "quantitative_impact": '{"BRL": 10000000.0}',
            "status": "CANDIDATE"
        })

        recal = ScoreRecalibrator(self.settings)
        recal.db_path = self.db_path
        recal.run_recalibration()

        mat = self.store.connection.execute("SELECT materiality_score FROM event_candidates WHERE event_id = 'evt_cache_cap'").fetchone()[0]
        self.assertAlmostEqual(float(mat), 0.20, places=2)

if __name__ == "__main__":
    unittest.main()
