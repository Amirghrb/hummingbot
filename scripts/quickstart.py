import logging
from decimal import Decimal
from typing import List

from hummingbot.core.data_type.common import OrderType, PriceType, TradeType
from hummingbot.core.data_type.order_candidate import OrderCandidate
from hummingbot.core.event.events import OrderFilledEvent
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase


class QuickStart(ScriptStrategyBase):
    pair="cleg-usdt"
    markets ={
        "xt":{pair}
    }
    def on_tick(self):
        price = self.connectors["xt"].get_mid_price(self.pair)
        msg = f"CLEG price is --------------------\n {price}------------------" 
        self.logger().info(msg)
