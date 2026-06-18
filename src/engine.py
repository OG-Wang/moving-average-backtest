"""回测引擎：把"持仓信号 + 价格"转成逐日权益曲线与交易明细。

与具体策略解耦——只吃 0/1 信号和 OHLC 数据。支持两种成交时点：
  - exec='close'    ：信号当日以收盘价成交（默认；含轻微未来函数，日线回测常规简化）
  - exec='next_open'：信号次日以开盘价成交（更贴近实盘，无未来函数）

成本：买入与卖出各收取一次 commission（默认万分之一）+ 可选 slippage（单边），以乘法方式作用于权益。

绩效统计只从 stats_start 起算；传入的数据可包含其之前的均线预热段，引擎会自动跳过。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class BacktestResult:
    equity: pd.Series          # 策略权益曲线（起点=1.0）
    buy_hold: pd.Series        # 买入持有基准权益曲线（起点=1.0）
    daily_returns: pd.Series   # 策略逐日净收益率
    trades: pd.DataFrame       # 交易明细
    position: pd.Series        # 每个交易日实际持仓 0/1（用于画买卖点）
    ma: pd.Series | None = None
    meta: dict = field(default_factory=dict)


class BacktestEngine:
    def __init__(
        self,
        commission: float = 0.0001,
        slippage: float = 0.0,
        exec_mode: str = "close",
    ):
        if exec_mode not in ("close", "next_open"):
            raise ValueError("exec_mode 必须是 'close' 或 'next_open'")
        self.commission = float(commission)
        self.slippage = float(slippage)
        if not np.isfinite(self.commission) or not np.isfinite(self.slippage):
            raise ValueError("commission/slippage 必须是有限数字")
        if self.commission < 0:
            raise ValueError("commission 必须 >= 0")
        if self.slippage < 0:
            raise ValueError("slippage 必须 >= 0")
        self.exec_mode = exec_mode
        self.cost = self.commission + self.slippage  # 单边总成本率
        if self.cost >= 1:
            raise ValueError("commission + slippage 必须 < 1")

    def run(
        self,
        df: pd.DataFrame,
        signal: pd.Series,
        stats_start: str | None = None,
        ma: pd.Series | None = None,
    ) -> BacktestResult:
        # 绩效区间：跳过均线预热段，只从 stats_start 起
        if stats_start is not None:
            mask = df.index >= pd.to_datetime(stats_start)
            df = df.loc[mask]
            signal = signal.loc[mask]
            if ma is not None:
                ma = ma.loc[mask]
        if len(df) < 2:
            raise ValueError("回测区间数据不足（少于 2 个交易日）")

        sig = signal.reindex(df.index).fillna(0).astype(int).to_numpy()
        o = df["open"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
        dates = df.index
        n = len(df)
        cost = self.cost

        equity = np.ones(n)
        pos_held = np.zeros(n, dtype=int)   # 当日实际持仓状态（收盘时）
        trades = []

        in_pos = False
        entry_idx = None
        entry_price = None
        eq = 1.0

        if self.exec_mode == "close":
            # 当日收盘价决策并成交
            for t in range(n):
                if t > 0:
                    # 第 t 日 close-to-close 收益，按"进入该日时的持仓"（即昨日收盘后的状态）累计
                    if in_pos:
                        eq *= c[t] / c[t - 1]
                # 处理第 t 日收盘信号
                target = sig[t]
                if target == 1 and not in_pos:
                    eq *= (1 - cost)            # 买入成本
                    in_pos, entry_idx, entry_price = True, t, c[t]
                elif target == 0 and in_pos:
                    eq *= (1 - cost)            # 卖出成本
                    trades.append(self._close_trade(dates, entry_idx, entry_price, t, c[t]))
                    in_pos = False
                equity[t] = eq
                pos_held[t] = 1 if in_pos else 0
        else:
            # next_open：信号次日以开盘价成交。第 t 日先按"昨日收盘信号"在开盘执行，再累计当日收益
            for t in range(n):
                if t > 0:
                    # 隔夜收益 close[t-1] -> open[t]，按昨日收盘时的持仓累计
                    if in_pos:
                        eq *= o[t] / c[t - 1]
                    # 在 open[t] 按昨日(t-1)收盘产生的信号执行
                    target = sig[t - 1]
                    if target == 1 and not in_pos:
                        eq *= (1 - cost)
                        in_pos, entry_idx, entry_price = True, t, o[t]
                    elif target == 0 and in_pos:
                        eq *= (1 - cost)
                        trades.append(self._close_trade(dates, entry_idx, entry_price, t, o[t]))
                        in_pos = False
                    # 日内收益 open[t] -> close[t]
                    if in_pos:
                        eq *= c[t] / o[t]
                equity[t] = eq
                pos_held[t] = 1 if in_pos else 0

        # 期末仍持仓：不强制平仓、不扣卖出成本；仅在交易明细里保留一条持仓中的盯市记录。
        if in_pos:
            trades.append(self._open_trade(dates, entry_idx, entry_price, n - 1, c[n - 1]))

        equity_s = pd.Series(equity, index=dates, name="strategy")
        daily_ret = equity_s.pct_change().fillna(0.0)
        buy_hold = pd.Series(c / c[0], index=dates, name="buy_hold")
        pos_s = pd.Series(pos_held, index=dates, name="position")

        trades_df = pd.DataFrame(
            trades,
            columns=["entry_date", "entry_price", "exit_date", "exit_price",
                     "holding_days", "return", "win", "is_open"],
        )

        return BacktestResult(
            equity=equity_s,
            buy_hold=buy_hold,
            daily_returns=daily_ret,
            trades=trades_df,
            position=pos_s,
            ma=ma,
            meta={
                "exec_mode": self.exec_mode,
                "commission": self.commission,
                "slippage": self.slippage,
                "start": str(dates[0].date()),
                "end": str(dates[-1].date()),
                "n_days": n,
            },
        )

    def _close_trade(self, dates, entry_idx, entry_price, exit_idx, exit_price) -> dict:
        # 单笔净收益：含买卖两次成本
        gross = exit_price / entry_price - 1.0
        net = (exit_price / entry_price) * (1 - self.cost) ** 2 - 1.0
        holding_days = int(exit_idx - entry_idx)  # 持仓交易日数
        return {
            "entry_date": dates[entry_idx].date(),
            "entry_price": round(float(entry_price), 3),
            "exit_date": dates[exit_idx].date(),
            "exit_price": round(float(exit_price), 3),
            "holding_days": holding_days,
            "return": net,
            "win": net > 0,
            "is_open": False,
        }

    def _open_trade(self, dates, entry_idx, entry_price, last_idx, last_price) -> dict:
        # 未平仓持仓按最后一日收盘价盯市；收益含买入成本，不含卖出成本。
        mtm = (last_price / entry_price) * (1 - self.cost) - 1.0
        holding_days = int(last_idx - entry_idx)
        return {
            "entry_date": dates[entry_idx].date(),
            "entry_price": round(float(entry_price), 3),
            "exit_date": pd.NA,
            "exit_price": pd.NA,
            "holding_days": holding_days,
            "return": mtm,
            "win": mtm > 0,
            "is_open": True,
        }
