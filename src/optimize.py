"""MA 参数寻优：在「买入均线 × 卖出均线」二维网格上全扫，挑选较优的均线组合。

由图形界面的「MA 参数寻优」(START:END:STEP) 触发，经 core.execute 调用。复用 strategy / engine / metrics，不引入新逻辑。

寻优逻辑：
  - 扫描区间含 K 个候选周期，则共回测 K×K 个 (买入MA, 卖出MA) 组合（买卖相互独立，含 买>卖 / 买<卖 / 买=卖）。
  - 展示口径：取「夏普比率 Top20」∪「总收益率 Top20」两份名单的并集去重，故最终行数在 20~40 之间。
    每行标注入选原因（夏普Top20 / 收益Top20 / 两榜兼入）。
"""

from __future__ import annotations

import pandas as pd

from strategy import MovingAverageStrategy
from engine import BacktestEngine
from metrics import compute_metrics


def parse_spec(spec: str) -> range:
    """解析 'START:END:STEP'（含端点）为 range；STEP 省略默认为 1。"""
    parts = [int(x) for x in str(spec).split(":")]
    if len(parts) == 2:
        start, end, step = parts[0], parts[1], 1
    elif len(parts) == 3:
        start, end, step = parts
    else:
        raise ValueError("--optimize 格式应为 START:END[:STEP]，如 5:120:5")
    if start < 1 or end < start or step < 1:
        raise ValueError(f"--optimize 区间非法：{spec}")
    return range(start, end + 1, step)


def optimize_ma(
    df: pd.DataFrame,
    windows: range,
    stats_start: str,
    commission: float = 0.0001,
    slippage: float = 0.0,
    exec_mode: str = "close",
    rf: float = 0.0,
    periods_per_year: float = 252,
    top_n: int = 20,
) -> pd.DataFrame:
    """在 买入×卖出 均线网格上全扫，返回「夏普Top20 ∪ 收益Top20」的并集去重表。

    periods_per_year：每年 bar 数，日线 252、日内须传入（年化/夏普折算用）。
    top_n：每个榜单各取前 N（默认 20）。返回表按夏普降序排列，含 in_sharpe/in_return/reason 三列。
    """
    rows = []
    for buy in windows:
        for sell in windows:
            strat = MovingAverageStrategy(buy, sell)
            sig = strat.generate_signals(df)
            try:
                res = BacktestEngine(commission, slippage, exec_mode).run(df, sig, stats_start=stats_start)
                m = compute_metrics(res.equity, res.daily_returns, res.trades, res.buy_hold,
                                    rf=rf, periods_per_year=periods_per_year)
                rows.append({
                    "ma_buy": buy,
                    "ma_sell": sell,
                    "total_return": m["total_return"],
                    "annual_return": m["annual_return"],
                    "max_drawdown": m["max_drawdown"],
                    "sharpe": m["sharpe"],
                    "calmar": m["calmar"],
                    "n_trades": m["n_trades"],
                    "win_rate": m["win_rate"],
                })
            except Exception as e:  # noqa: BLE001
                print(f"  买MA{buy}/卖MA{sell} 跳过：{type(e).__name__}: {e}")

    full = pd.DataFrame(rows)
    if full.empty:
        return full

    # 两个榜单各取 Top N（去掉 NaN 的夏普以免污染排序），再取并集
    sharpe_top = set(
        full.dropna(subset=["sharpe"]).sort_values("sharpe", ascending=False)
        .head(top_n).index
    )
    return_top = set(full.sort_values("total_return", ascending=False).head(top_n).index)
    keep = sharpe_top | return_top

    out = full.loc[sorted(keep)].copy()
    out["in_sharpe"] = out.index.isin(sharpe_top)
    out["in_return"] = out.index.isin(return_top)

    def _reason(r):
        if r["in_sharpe"] and r["in_return"]:
            return "两榜兼入"
        return "夏普Top20" if r["in_sharpe"] else "收益Top20"

    out["reason"] = out.apply(_reason, axis=1)
    # 默认按夏普降序展示；夏普相同/缺失时退而看总收益
    out = out.sort_values(["sharpe", "total_return"], ascending=False).reset_index(drop=True)
    return out
