"""绩效指标计算：由权益曲线 / 逐日收益 / 交易明细汇总全部指标。"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def max_drawdown(equity: pd.Series) -> tuple[float, pd.Timestamp | None, pd.Timestamp | None]:
    """返回 (最大回撤(<=0), 回撤峰值日, 回撤谷值日)。"""
    roll_max = equity.cummax()
    dd = equity / roll_max - 1.0
    trough = dd.idxmin()
    mdd = float(dd.min())
    if mdd == 0:
        return 0.0, None, None
    peak = equity.loc[:trough].idxmax()
    return mdd, peak, trough


def drawdown_series(equity: pd.Series) -> pd.Series:
    return equity / equity.cummax() - 1.0


def _annualized(total_return: float, n_days: int, periods_per_year: float = TRADING_DAYS) -> float:
    if n_days <= 0:
        return 0.0
    return (1.0 + total_return) ** (periods_per_year / n_days) - 1.0


def yearly_returns(daily_returns: pd.Series) -> pd.Series:
    """按自然年复利汇总策略收益。"""
    return daily_returns.groupby(daily_returns.index.year).apply(lambda r: (1 + r).prod() - 1.0)


def monthly_returns_table(daily_returns: pd.Series) -> pd.DataFrame:
    """返回 行=年 列=月(1..12) 的月度收益率矩阵，供热力图使用。"""
    g = daily_returns.groupby([daily_returns.index.year, daily_returns.index.month])
    m = g.apply(lambda r: (1 + r).prod() - 1.0)
    m.index.names = ["year", "month"]
    table = m.unstack("month")
    table = table.reindex(columns=range(1, 13))
    return table


def compute_metrics(
    equity: pd.Series,
    daily_returns: pd.Series,
    trades: pd.DataFrame,
    buy_hold: pd.Series,
    rf: float = 0.0,
    periods_per_year: float = TRADING_DAYS,
) -> dict:
    """汇总所有绩效指标，返回 dict。

    rf               : 年化无风险利率。
    periods_per_year : 每年的 bar 数，用于年化/夏普折算。日线=252；日内须传入（如 4h≈504）。
    """
    n = len(equity)
    total_return = float(equity.iloc[-1] - 1.0)
    ann_return = _annualized(total_return, n, periods_per_year)

    rf_daily = rf / periods_per_year
    excess = daily_returns - rf_daily
    std = float(daily_returns.std(ddof=1))  # 仅作夏普比率的分母用
    sharpe = float(excess.mean() / std * np.sqrt(periods_per_year)) if std > 0 else 0.0

    downside = daily_returns[daily_returns < 0]
    dstd = float(downside.std(ddof=1)) if len(downside) > 1 else 0.0
    sortino = float(excess.mean() / dstd * np.sqrt(periods_per_year)) if dstd > 0 else 0.0

    mdd, dd_peak, dd_trough = max_drawdown(equity)
    calmar = float(ann_return / abs(mdd)) if mdd < 0 else 0.0

    # 交易层面：未平仓持仓参与胜率/盈亏比，按当前盯市收益正负判断；
    # 但交易次数和持仓天数类指标仍只统计完整买卖回合。
    if "is_open" in trades.columns:
        open_mask = trades["is_open"].fillna(False).astype(bool)
        closed_trades = trades.loc[~open_mask]
        open_trades = int(open_mask.sum())
    else:
        closed_trades = trades
        open_trades = 0

    n_trades = int(len(closed_trades))
    scored_trades = trades
    n_scored = int(len(scored_trades))
    if n_scored > 0:
        wins = scored_trades[scored_trades["win"]]
        losses = scored_trades[~scored_trades["win"]]
        win_rate = len(wins) / n_scored
        avg_win = float(wins["return"].mean()) if len(wins) else 0.0
        avg_loss = float(losses["return"].mean()) if len(losses) else 0.0
        # 盈亏比 = 平均盈利 / 平均亏损绝对值
        profit_loss_ratio = float(avg_win / abs(avg_loss)) if avg_loss < 0 else float("inf")
    else:
        win_rate = avg_win = avg_loss = profit_loss_ratio = 0.0

    if n_trades > 0:
        hold = closed_trades["holding_days"]
        avg_hold, max_hold, min_hold = float(hold.mean()), int(hold.max()), int(hold.min())
    else:
        avg_hold = max_hold = min_hold = 0

    # 买入持有基准
    bh_total = float(buy_hold.iloc[-1] - 1.0)
    bh_ann = _annualized(bh_total, n, periods_per_year)
    bh_mdd, _, _ = max_drawdown(buy_hold)

    return {
        "total_return": total_return,
        "annual_return": ann_return,
        "max_drawdown": mdd,
        "dd_peak": dd_peak,
        "dd_trough": dd_trough,
        "sharpe": sharpe,
        "sortino": sortino,
        "calmar": calmar,
        "n_trades": n_trades,
        "open_trades": open_trades,
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_loss_ratio": profit_loss_ratio,
        "avg_holding_days": avg_hold,
        "max_holding_days": max_hold,
        "min_holding_days": min_hold,
        "bh_total_return": bh_total,
        "bh_annual_return": bh_ann,
        "bh_max_drawdown": bh_mdd,
        "n_days": n,
        "rf": rf,
    }
