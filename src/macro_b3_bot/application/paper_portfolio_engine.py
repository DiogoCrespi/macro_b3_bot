"""Sprint 4G Paper Portfolio Engine.

Manages simulated allocation events, position tracking, cash balances, transaction cost modeling,
and mark-to-market NAV calculations without order execution or real brokerage interactions.

Outputs are strictly simulated allocation events (SIMULATED_ENTRY, SIMULATED_EXIT, KEEP_OPEN, NO_ALLOCATION).
"""

from datetime import datetime
from macro_b3_bot.domain.research_decision_models import ResearchDecisionSnapshot
from macro_b3_bot.domain.research_timing_risk_models import ResearchTimingRiskSnapshot
from macro_b3_bot.domain.paper_portfolio_models import (
    PaperAllocationEvent,
    PaperPortfolioPolicy,
    PaperPortfolioSnapshot,
    PaperPositionSnapshot,
)


class PaperPortfolioEngine:
    """
    Simulated portfolio management engine.
    """
    def __init__(self, policy: PaperPortfolioPolicy, portfolio_id: str = "pilot_paper_portfolio_001"):
        self.policy = policy
        self.portfolio_id = portfolio_id
        self.cash_balance = policy.initial_capital
        self.positions: dict[str, PaperPositionSnapshot] = {}
        self.total_realized_pnl = 0.0
        self.total_transaction_costs = 0.0
        self.total_slippage = 0.0
        self.allocation_history: list[PaperAllocationEvent] = []

    def evaluate_and_allocate(
        self,
        *,
        cutoff_dt: datetime,
        ticker: str,
        decision_snapshot: ResearchDecisionSnapshot,
        timing_snapshot: ResearchTimingRiskSnapshot,
        execution_session_date: str,
        execution_price: float | None,
    ) -> PaperAllocationEvent:
        """
        Evaluates eligibility and executes simulated allocation event.
        Execution session must be strictly after decision cutoff date.
        """
        created_at_str = cutoff_dt.isoformat()
        is_open = ticker in self.positions and self.positions[ticker].status == "OPEN"

        # Check eligibility for simulated entry
        is_eligible_entry = (
            decision_snapshot.decision == "WATCH"
            and timing_snapshot.timing_classification == "MONITOR"
            and timing_snapshot.risk_classification not in ("HIGH_RISK", "UNACCEPTABLE_RISK")
            and decision_snapshot.execution_mode == "REAL_UPSTREAM_SYNTHESIS"
            and not decision_snapshot.critical_blockers
        )

        input_ids = {
            "research_decision_id": decision_snapshot.decision_id,
            "timing_risk_id": timing_snapshot.timing_risk_id,
            "macro_event_ids": decision_snapshot.macro_event_ids,
            "valuation_observation_ids": decision_snapshot.input_ids.get("valuation_observation_ids", []),
        }

        # Case 1: Open position requiring simulated exit
        if is_open:
            pos = self.positions[ticker]
            should_exit = False
            exit_reason = ""
            exit_event_type = "SIMULATED_EXIT"

            if decision_snapshot.decision == "NO_ACTION":
                should_exit = True
                exit_reason = f"Decision changed to NO_ACTION (blockers: {decision_snapshot.critical_blockers})"
            elif timing_snapshot.timing_classification in ("WAIT_FOR_CONFIRMATION", "AVOID"):
                should_exit = True
                exit_reason = f"Timing changed to {timing_snapshot.timing_classification}"
                if timing_snapshot.timing_classification == "AVOID":
                    exit_event_type = "INVALIDATION_EXIT"
            elif timing_snapshot.risk_classification in ("HIGH_RISK", "UNACCEPTABLE_RISK"):
                should_exit = True
                exit_reason = f"Risk severity escalated to {timing_snapshot.risk_classification}"
                exit_event_type = "INVALIDATION_EXIT"

            if should_exit:
                if execution_price is None or execution_price <= 0:
                    execution_price = pos.current_price

                gross_val = pos.quantity * execution_price
                cost = gross_val * (self.policy.b3_emoluments_pct + self.policy.brokerage_fee)
                slippage = gross_val * self.policy.base_slippage_pct
                net_val = gross_val - cost - slippage

                realized_pnl = net_val - (pos.quantity * pos.average_entry_price)
                self.cash_balance += net_val
                self.total_realized_pnl += realized_pnl
                self.total_transaction_costs += cost
                self.total_slippage += slippage

                pos.status = "CLOSED"
                pos.weight = 0.0

                event_payload = {
                    "portfolio_id": self.portfolio_id,
                    "ticker": ticker,
                    "event_type": exit_event_type,
                    "research_decision_id": decision_snapshot.decision_id,
                    "timing_risk_id": timing_snapshot.timing_risk_id,
                    "decision_available_at": decision_snapshot.as_of_timestamp,
                    "execution_session": execution_session_date,
                    "execution_price": execution_price,
                    "target_weight": 0.0,
                    "executed_weight": 0.0,
                    "quantity_simulated": pos.quantity,
                    "gross_value": gross_val,
                    "transaction_cost": round(cost, 4),
                    "slippage_cost": round(slippage, 4),
                    "reason": exit_reason,
                    "input_ids": input_ids,
                    "created_at": created_at_str,
                }
                evt_id = PaperAllocationEvent.compute_event_id(event_payload)
                evt = PaperAllocationEvent(allocation_event_id=evt_id, **event_payload)
                self.allocation_history.append(evt)
                return evt
            else:
                # Keep position open
                event_payload = {
                    "portfolio_id": self.portfolio_id,
                    "ticker": ticker,
                    "event_type": "KEEP_OPEN",
                    "research_decision_id": decision_snapshot.decision_id,
                    "timing_risk_id": timing_snapshot.timing_risk_id,
                    "decision_available_at": decision_snapshot.as_of_timestamp,
                    "execution_session": execution_session_date,
                    "execution_price": execution_price or pos.current_price,
                    "target_weight": pos.weight,
                    "executed_weight": pos.weight,
                    "quantity_simulated": pos.quantity,
                    "gross_value": pos.quantity * (execution_price or pos.current_price),
                    "transaction_cost": 0.0,
                    "slippage_cost": 0.0,
                    "reason": "Position remains eligible; maintaining position.",
                    "input_ids": input_ids,
                    "created_at": created_at_str,
                }
                evt_id = PaperAllocationEvent.compute_event_id(event_payload)
                evt = PaperAllocationEvent(allocation_event_id=evt_id, **event_payload)
                self.allocation_history.append(evt)
                return evt

        # Case 2: Eligible for SIMULATED_ENTRY
        if is_eligible_entry and not is_open:
            if execution_price is None or execution_price <= 0:
                event_payload = {
                    "portfolio_id": self.portfolio_id,
                    "ticker": ticker,
                    "event_type": "NO_ALLOCATION",
                    "research_decision_id": decision_snapshot.decision_id,
                    "timing_risk_id": timing_snapshot.timing_risk_id,
                    "decision_available_at": decision_snapshot.as_of_timestamp,
                    "execution_session": execution_session_date,
                    "execution_price": None,
                    "target_weight": 0.0,
                    "executed_weight": 0.0,
                    "quantity_simulated": 0.0,
                    "gross_value": 0.0,
                    "transaction_cost": 0.0,
                    "slippage_cost": 0.0,
                    "reason": "PAPER_EXECUTION_BLOCKED_MISSING_PRICE",
                    "input_ids": input_ids,
                    "created_at": created_at_str,
                }
                evt_id = PaperAllocationEvent.compute_event_id(event_payload)
                evt = PaperAllocationEvent(allocation_event_id=evt_id, **event_payload)
                self.allocation_history.append(evt)
                return evt

            current_nav = self.get_current_nav()
            target_weight = min(self.policy.max_weight_per_asset, 0.20)
            allocated_capital = current_nav * target_weight

            if self.cash_balance < allocated_capital:
                allocated_capital = max(0.0, self.cash_balance - (current_nav * self.policy.min_cash_weight))

            if allocated_capital <= 0 or (allocated_capital / current_nav) < self.policy.min_position_weight:
                event_payload = {
                    "portfolio_id": self.portfolio_id,
                    "ticker": ticker,
                    "event_type": "NO_ALLOCATION",
                    "research_decision_id": decision_snapshot.decision_id,
                    "timing_risk_id": timing_snapshot.timing_risk_id,
                    "decision_available_at": decision_snapshot.as_of_timestamp,
                    "execution_session": execution_session_date,
                    "execution_price": execution_price,
                    "target_weight": target_weight,
                    "executed_weight": 0.0,
                    "quantity_simulated": 0.0,
                    "gross_value": 0.0,
                    "transaction_cost": 0.0,
                    "slippage_cost": 0.0,
                    "reason": "Insufficient cash balance or position size below minimum threshold.",
                    "input_ids": input_ids,
                    "created_at": created_at_str,
                }
                evt_id = PaperAllocationEvent.compute_event_id(event_payload)
                evt = PaperAllocationEvent(allocation_event_id=evt_id, **event_payload)
                self.allocation_history.append(evt)
                return evt

            quantity = allocated_capital / execution_price
            gross_val = quantity * execution_price
            cost = gross_val * (self.policy.b3_emoluments_pct + self.policy.brokerage_fee)
            slippage = gross_val * self.policy.base_slippage_pct
            total_outlay = gross_val + cost + slippage

            self.cash_balance -= total_outlay
            self.total_transaction_costs += cost
            self.total_slippage += slippage

            executed_weight = gross_val / current_nav

            self.positions[ticker] = PaperPositionSnapshot(
                position_id=f"pos_{ticker}_{int(cutoff_dt.timestamp())}",
                portfolio_id=self.portfolio_id,
                ticker=ticker,
                as_of_timestamp=created_at_str,
                quantity=quantity,
                average_entry_price=execution_price,
                current_price=execution_price,
                market_value=gross_val,
                unrealized_pnl=0.0,
                weight=executed_weight,
                status="OPEN",
            )

            event_payload = {
                "portfolio_id": self.portfolio_id,
                "ticker": ticker,
                "event_type": "SIMULATED_ENTRY",
                "research_decision_id": decision_snapshot.decision_id,
                "timing_risk_id": timing_snapshot.timing_risk_id,
                "decision_available_at": decision_snapshot.as_of_timestamp,
                "execution_session": execution_session_date,
                "execution_price": execution_price,
                "target_weight": target_weight,
                "executed_weight": round(executed_weight, 4),
                "quantity_simulated": round(quantity, 4),
                "gross_value": round(gross_val, 2),
                "transaction_cost": round(cost, 4),
                "slippage_cost": round(slippage, 4),
                "reason": "Satisfied all entry eligibility gates (WATCH + MONITOR + LOW/MODERATE Risk + REAL Mode + Zero Blockers).",
                "input_ids": input_ids,
                "created_at": created_at_str,
            }
            evt_id = PaperAllocationEvent.compute_event_id(event_payload)
            evt = PaperAllocationEvent(allocation_event_id=evt_id, **event_payload)
            self.allocation_history.append(evt)
            return evt

        # Case 3: Ineligible for entry -> NO_ALLOCATION
        reasons = []
        if decision_snapshot.decision != "WATCH":
            reasons.append(f"Decision={decision_snapshot.decision}")
        if timing_snapshot.timing_classification != "MONITOR":
            reasons.append(f"Timing={timing_snapshot.timing_classification}")
        if timing_snapshot.risk_classification in ("HIGH_RISK", "UNACCEPTABLE_RISK"):
            reasons.append(f"Risk={timing_snapshot.risk_classification}")
        if decision_snapshot.execution_mode != "REAL_UPSTREAM_SYNTHESIS":
            reasons.append(f"Mode={decision_snapshot.execution_mode}")
        if decision_snapshot.critical_blockers:
            reasons.append(f"Blockers={decision_snapshot.critical_blockers}")

        reason_str = "Ineligible for simulated entry: " + ", ".join(reasons)

        event_payload = {
            "portfolio_id": self.portfolio_id,
            "ticker": ticker,
            "event_type": "NO_ALLOCATION",
            "research_decision_id": decision_snapshot.decision_id,
            "timing_risk_id": timing_snapshot.timing_risk_id,
            "decision_available_at": decision_snapshot.as_of_timestamp,
            "execution_session": execution_session_date,
            "execution_price": execution_price,
            "target_weight": 0.0,
            "executed_weight": 0.0,
            "quantity_simulated": 0.0,
            "gross_value": 0.0,
            "transaction_cost": 0.0,
            "slippage_cost": 0.0,
            "reason": reason_str,
            "input_ids": input_ids,
            "created_at": created_at_str,
        }
        evt_id = PaperAllocationEvent.compute_event_id(event_payload)
        evt = PaperAllocationEvent(allocation_event_id=evt_id, **event_payload)
        self.allocation_history.append(evt)
        return evt

    def mark_to_market(self, as_of_dt: datetime, current_prices: dict[str, float]) -> PaperPortfolioSnapshot:
        """
        Updates mark-to-market values for all open positions and calculates daily NAV.
        """
        positions_val = 0.0
        open_count = 0

        for ticker, pos in self.positions.items():
            if pos.status == "OPEN":
                open_count += 1
                if ticker in current_prices and current_prices[ticker] > 0:
                    pos.current_price = current_prices[ticker]
                pos.market_value = pos.quantity * pos.current_price
                pos.unrealized_pnl = (pos.current_price - pos.average_entry_price) * pos.quantity
                positions_val += pos.market_value

        nav = self.cash_balance + positions_val

        # Recalculate weights
        for pos in self.positions.values():
            if pos.status == "OPEN":
                pos.weight = round(pos.market_value / nav, 4) if nav > 0 else 0.0

        daily_pnl = nav - self.policy.initial_capital - self.total_realized_pnl

        return PaperPortfolioSnapshot(
            portfolio_snapshot_id=f"nav_{int(as_of_dt.timestamp())}",
            portfolio_id=self.portfolio_id,
            as_of_timestamp=as_of_dt.isoformat(),
            cash_balance=round(self.cash_balance, 2),
            positions_value=round(positions_val, 2),
            nav=round(nav, 2),
            daily_pnl=round(daily_pnl, 2),
            total_realized_pnl=round(self.total_realized_pnl, 2),
            total_transaction_costs=round(self.total_transaction_costs, 4),
            total_slippage=round(self.total_slippage, 4),
            open_positions_count=open_count,
        )

    def get_current_nav(self) -> float:
        pos_val = sum(p.market_value for p in self.positions.values() if p.status == "OPEN")
        return self.cash_balance + pos_val
