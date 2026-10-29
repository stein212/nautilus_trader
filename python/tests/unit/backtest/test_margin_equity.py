# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------
"""
Test margin equity valuation with trade-only market data.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Self

import pytest

from nautilus_trader.backtest import BacktestEngine
from nautilus_trader.backtest import BacktestEngineConfig
from nautilus_trader.core import UUID4
from nautilus_trader.execution import MakerTakerFeeModel
from nautilus_trader.model import AccountType
from nautilus_trader.model import AggressorSide
from nautilus_trader.model import ClientOrderId
from nautilus_trader.model import Currency
from nautilus_trader.model import InstrumentId
from nautilus_trader.model import MarketOrder
from nautilus_trader.model import Money
from nautilus_trader.model import OmsType
from nautilus_trader.model import OrderSide
from nautilus_trader.model import Price
from nautilus_trader.model import Quantity
from nautilus_trader.model import TimeInForce
from nautilus_trader.model import TradeId
from nautilus_trader.model import TradeTick
from nautilus_trader.model import Venue
from nautilus_trader.portfolio import PortfolioConfig
from nautilus_trader.trading import Strategy
from tests.providers import TestInstrumentProvider


USDT = Currency.from_str("USDT")
BINANCE = Venue("BINANCE")


class MarginEquityProbe(Strategy):
    """
    Prime PnL once, then record equity without refreshing PnL after later trades.
    """

    def __new__(cls, _instrument_id: InstrumentId, _side: OrderSide) -> Self:
        """
        Construct the native base without forwarding probe-only arguments.
        """
        return super().__new__(cls)

    def __init__(self, instrument_id: InstrumentId, side: OrderSide) -> None:
        """
        Initialize isolated observations for one backtest run.
        """
        super().__init__()
        self._instrument_id = instrument_id
        self._side = side
        self._trade_count = 0
        self.primed_pnl = None
        self.primed_equity = None
        self.observations = []

    def on_start(self) -> None:
        """
        Subscribe only to trades.
        """
        self.subscribe_trades(self._instrument_id)

    def on_trade(self, trade: TradeTick) -> None:
        """
        Enter, prime the cache, then observe equity before any further PnL query.
        """
        self._trade_count += 1

        if self._trade_count == 1:
            self.submit_order(
                MarketOrder(
                    trader_id=self.trader_id,
                    strategy_id=self.strategy_id,
                    instrument_id=self._instrument_id,
                    client_order_id=ClientOrderId(f"{self.strategy_id}-1"),
                    order_side=self._side,
                    quantity=Quantity.from_str("1.000"),
                    init_id=UUID4(),
                    ts_init=self.clock.timestamp_ns(),
                    time_in_force=TimeInForce.GTC,
                    reduce_only=False,
                    quote_quantity=False,
                    contingency_type=None,
                ),
            )
        elif self._trade_count == 2:
            self.primed_pnl = self.portfolio.unrealized_pnl(self._instrument_id)
            self.primed_equity = self.portfolio.equity(venue=BINANCE)
        else:
            account = self.cache.account_for_venue(BINANCE)
            self.observations.append(
                (
                    trade.price,
                    self.portfolio.equity(venue=BINANCE),
                    self.portfolio.equity(venue=BINANCE),
                    self.portfolio.equity(account_id=account.id),
                    self.portfolio.equity(venue=BINANCE),
                ),
            )


@pytest.mark.parametrize("recalculate_margin_equity", [False, True])
@pytest.mark.parametrize("side", [OrderSide.BUY, OrderSide.SELL], ids=["long", "short"])
def test_trade_only_margin_equity_uses_current_price_when_enabled(
    recalculate_margin_equity: bool,
    side: OrderSide,
) -> None:
    """
    Fresh equity is opt-in; scoped reads stay fresh without updating the PnL cache.
    """
    instrument = TestInstrumentProvider.btcusdt_perp_binance()
    strategy = MarginEquityProbe(instrument.id, side)
    engine = BacktestEngine(
        BacktestEngineConfig(
            bypass_logging=True,
            run_analysis=False,
            portfolio=PortfolioConfig(
                equity_curve=False,
                recalculate_margin_equity=recalculate_margin_equity,
            ),
        ),
    )
    try:
        engine.add_venue(
            venue=BINANCE,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money.from_str("100_000 USDT")],
            base_currency=USDT,
            trade_execution=True,
            fee_model=MakerTakerFeeModel(maker_rate=Decimal(0), taker_rate=Decimal(0)),
        )
        engine.add_instrument(instrument)
        engine.add_strategy(strategy)
        trades = []

        for index, price in enumerate(("20000.0", "20000.0", "20100.0", "19900.0")):
            # 2020-01-01 UTC, with no day boundary to trigger a PnL refresh
            timestamp = 1_577_836_800_000_000_000 + index * 1_000_000_000
            trades.append(
                TradeTick(
                    instrument_id=instrument.id,
                    price=Price.from_str(price),
                    size=Quantity.from_str("10.000"),
                    aggressor_side=AggressorSide.NO_AGGRESSOR,
                    trade_id=TradeId(f"T-{index}"),
                    ts_event=timestamp,
                    ts_init=timestamp,
                ),
            )
        engine.add_data(trades)
        engine.run()

        balance = Money.from_str("100_000 USDT")
        assert strategy.primed_pnl == Money.from_str("0 USDT")
        assert strategy.primed_equity == {USDT: balance}
        assert engine.cache.account_for_venue(BINANCE).balance_total(USDT) == balance
        [position] = engine.cache.positions_open(instrument_id=instrument.id)
        assert position.quantity == Quantity.from_str("1.000")
        assert position.entry == side
        assert position.avg_px_open == Decimal("20000.0")

        direction = Decimal(1) if side == OrderSide.BUY else Decimal(-1)

        for observation, price in zip(
            strategy.observations,
            (Decimal("20100.0"), Decimal("19900.0")),
            strict=True,
        ):
            trade_price, equity, repeated, scoped, after_scoped = observation
            current_pnl = (price - Decimal("20000.0")) * direction
            fresh_equity = Money.from_decimal(Decimal(100000) + current_pnl, USDT)
            expected = {USDT: fresh_equity if recalculate_margin_equity else balance}
            assert trade_price.as_decimal() == price
            assert equity == expected
            assert repeated == expected
            assert scoped == {USDT: fresh_equity}
            assert after_scoped == expected

        # Only after the equity assertions may an on-demand PnL query refresh the cache
        final_pnl = Decimal(-100) * direction
        assert engine.portfolio.unrealized_pnl(instrument.id) == Money.from_decimal(final_pnl, USDT)
        assert engine.portfolio.equity(venue=BINANCE) == {
            USDT: Money.from_decimal(Decimal(100000) + final_pnl, USDT),
        }
    finally:
        engine.dispose()
