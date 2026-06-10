"""策略层：定义可扩展的策略接口与均线择时策略。

策略只负责产出"目标持仓信号"（每个交易日 1=持有 / 0=空仓），不关心成交价、成本与绩效，
这些由 engine.py 处理。这样新增策略时无需改动回测引擎。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd


class Strategy(ABC):
    """策略基类。子类实现 generate_signals，输入日线数据，输出 0/1 持仓信号序列。"""

    name: str = "Strategy"

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """返回与 df 同索引的持仓信号 Series（1=持有，0=空仓）。"""
        raise NotImplementedError


class MovingAverageStrategy(Strategy):
    """均线择时策略（买入、卖出可用不同周期的均线）。

    规则（状态机）：
      - 买入均线 MA(buy_window)、卖出均线 MA(sell_window) 各按收盘价独立计算。
      - 空仓时：收盘价 > 买入均线 →（若启用 require_ma_order，还需买入均线 > 卖出均线）→ 次态持有（买入）。
      - 持仓时：收盘价 < 卖出均线 → 次态空仓（卖出）。
      - 其余情形（含价格落在两条均线之间、或恰等于均线）维持前一日持仓，避免抖动。
      - 任一所需均线尚未成形（前若干日为 NaN）时强制空仓。
    当 buy_window == sell_window 时，退化为原先"上穿买入、下穿卖出"的单均线策略。
    返回的信号代表"该日收盘后应处于的持仓状态"，成交时点由引擎按 exec 模式决定。

    require_ma_order（默认 False）：
      买入时额外要求"买入均线 > 卖出均线"。该条件主要用于 买入周期 < 卖出周期 的情形——
      避免价格刚上穿较快的买入均线、却仍在较慢的卖出均线下方，从而买入次日即触发卖出的"一日游"。
      当买入均线天然在卖出均线上方（如买入周期 >= 卖出周期的多数时段）时，此条件几乎不改变结果。
    """

    def __init__(self, buy_window: int = 20, sell_window: int | None = None,
                 require_ma_order: bool = False):
        if sell_window is None:
            sell_window = buy_window
        if buy_window < 1 or sell_window < 1:
            raise ValueError("均线周期必须 >= 1")
        self.buy_window = buy_window
        self.sell_window = sell_window
        self.require_ma_order = require_ma_order
        # window 保留为买入均线周期，兼容仅关心单一周期的旧调用方
        self.window = buy_window
        # 顺势过滤仅在「买入周期 < 卖出周期」时有意义；其余情形视为无害不启用
        self._order_active = require_ma_order and buy_window < sell_window
        self.name = (f"MA{buy_window}" if buy_window == sell_window
                     else f"MA买{buy_window}/卖{sell_window}")
        if self._order_active:
            self.name += "(顺势)"

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        ma_buy = close.rolling(self.buy_window).mean()
        ma_sell = close.rolling(self.sell_window).mean()

        c = close.to_numpy(dtype=float)
        mb = ma_buy.to_numpy(dtype=float)
        ms = ma_sell.to_numpy(dtype=float)
        n = len(c)
        out = np.zeros(n, dtype=int)
        require_order = self._order_active

        held = False
        for i in range(n):
            if np.isnan(mb[i]) or np.isnan(ms[i]):
                # 任一均线未成形：保持空仓
                held = False
            elif held:
                if c[i] < ms[i]:
                    held = False
            else:
                can_buy = c[i] > mb[i]
                if require_order and mb[i] <= ms[i]:
                    # 启用顺势过滤：买入均线未站上卖出均线时不买
                    can_buy = False
                if can_buy:
                    held = True
            out[i] = 1 if held else 0

        signal = pd.Series(out, index=df.index, name="signal")
        # 附带买入均线列供报告/引擎使用（不影响信号本身）
        self.ma = ma_buy.rename(f"MA{self.buy_window}")
        self.ma_buy = ma_buy.rename(f"MA{self.buy_window}")
        self.ma_sell = ma_sell.rename(f"MA{self.sell_window}")
        return signal.astype(int)
