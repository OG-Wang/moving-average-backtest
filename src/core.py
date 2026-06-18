"""回测核心编排：把数据 / 策略 / 引擎 / 指标 / 报告串联起来。

对外主入口 execute(args)：根据参数对象运行回测并生成 HTML 报告。
由图形界面 app.py 调用（args.save=False 时只在内存生成 HTML、不落盘）。
"""

from __future__ import annotations

import sys
import math
from types import SimpleNamespace

# 让 Windows 控制台也能正确显示中文
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from data import fetch_bars, _is_us, daily_symbol_label
from strategy import MovingAverageStrategy
from engine import BacktestEngine
from metrics import compute_metrics
from optimize import parse_spec, optimize_ma
from report import render_html

# 常见 A 股指数代码 -> 名称（仅用于报告显示，未列出的回退到代码本身）
INDEX_NAMES = {
    "000001": "上证指数", "000016": "上证50", "000300": "沪深300",
    "000688": "科创50", "000852": "中证1000", "000905": "中证500",
    "399001": "深证成指", "399006": "创业板指", "399300": "沪深300",
    "399905": "中证500", "899050": "北证50",
}


def index_name(code: str) -> str:
    # 美股直接显示大写代码（如 SOXL）；A 股指数查表，未命中回退代码本身
    special = daily_symbol_label(code)
    if special:
        return special
    if _is_us(code):
        return str(code).upper()
    c = str(code).lower()
    for prefix in ("sh", "sz", "bj"):
        if c.startswith(prefix):
            c = c[len(prefix):]
            break
    extra = {
        "n225": "日经225",
        "nikkei225": "日经225",
        "hsi": "恒生指数",
        "hstech": "恒生科技",
    }
    return extra.get(c, INDEX_NAMES.get(c, code))


def _periods_per_year(timeframe: str, idx) -> float:
    """每年 bar 数，用于年化/夏普折算。日线固定 252；日内按数据时间跨度推算（含夜盘/周末间隔）。"""
    if timeframe in (None, "1d", "日线"):
        return 252.0
    if len(idx) < 2:
        return 252.0
    span_years = max((idx[-1] - idx[0]).total_seconds() / (365.25 * 86400), 1e-9)
    return len(idx) / span_years


def _ma_pair(args) -> tuple[int, int]:
    """从 args 解析买入/卖出均线周期。

    优先用 ma_buy/ma_sell；缺省则回退到单一 ma（买卖同周期），兼容旧调用方。
    """
    buy = getattr(args, "ma_buy", None)
    sell = getattr(args, "ma_sell", None)
    if buy is None:
        buy = args.ma
    if sell is None:
        sell = buy
    return int(buy), int(sell)


def _sell_buffer(args) -> float:
    """从 args 解析卖出缓冲比例（小数，如 0.02）。缺省视为 0（不启用）。"""
    try:
        v = float(getattr(args, "sell_buffer", 0.0) or 0.0)
    except (TypeError, ValueError):
        raise ValueError("卖出缓冲比例必须是数字")
    if not math.isfinite(v) or v < 0 or v >= 1:
        raise ValueError("卖出缓冲比例必须在 0 到小于 1 之间")
    return v if v > 0 else 0.0


def _ma_label(buy: int, sell: int, require_order: bool = False,
              sell_buffer: float = 0.0) -> str:
    """统一的均线描述文案：同周期显示 MAN，不同周期显示 买MAx/卖MAy；顺势过滤、卖出缓冲生效时加后缀。

    顺势过滤仅在 买入周期 < 卖出周期 时生效（与 strategy 一致），其余视为无害不启用。
    """
    base = f"MA{buy}" if buy == sell else f"买MA{buy}/卖MA{sell}"
    if require_order and buy < sell:
        base += "（顺势）"
    if sell_buffer > 0:
        base += f"（缓冲{sell_buffer*100:g}%）"
    return base


def run_one(symbol: str, args) -> dict:
    """对单个标的跑回测，返回供报告使用的 panel。"""
    timeframe = getattr(args, "timeframe", "1d") or "1d"
    buy, sell = _ma_pair(args)
    require_order = bool(getattr(args, "require_ma_order", False))
    sell_buffer = _sell_buffer(args)
    longest = max(buy, sell)
    warmup = max(2 * longest, longest + 5)
    df = fetch_bars(symbol, timeframe, start=args.start, end=args.end,
                    warmup=warmup, refresh=args.refresh)
    strat = MovingAverageStrategy(buy, sell, require_ma_order=require_order,
                                  sell_buffer=sell_buffer)
    sig = strat.generate_signals(df)
    eng = BacktestEngine(commission=args.commission, slippage=args.slippage,
                         exec_mode=args.exec)
    res = eng.run(df, sig, stats_start=args.start, ma=strat.ma)
    ppy = _periods_per_year(timeframe, res.daily_returns.index)
    m = compute_metrics(res.equity, res.daily_returns, res.trades, res.buy_hold,
                        rf=args.rf, periods_per_year=ppy)
    name = index_name(symbol)
    tf_tag = "" if timeframe in ("1d", "日线") else f" {timeframe}"
    open_note = f"  持仓中 {m.get('open_trades', 0)} 笔" if m.get("open_trades", 0) else ""
    print(f"  {name}({symbol}){tf_tag} {_ma_label(buy, sell, require_order, sell_buffer)}: 总收益 {m['total_return']*100:.1f}%  "
          f"年化 {m['annual_return']*100:.1f}%  回撤 {m['max_drawdown']*100:.1f}%  "
          f"夏普 {m['sharpe']:.2f}  交易 {m['n_trades']} 笔{open_note}")
    return {"symbol": symbol, "label": f"{name}({symbol})", "res": res,
            "metrics": m, "df": df}


def execute(args) -> SimpleNamespace:
    """运行回测并生成报告。

    args 需含属性：symbol/ma/start/end/commission/slippage/rf/exec/optimize/refresh，
    以及可选 output（HTML 路径）与 save（是否落盘，默认 True）。
    返回 SimpleNamespace(html=报告HTML字符串, path=写入的文件路径或 None)。
    """
    symbols = [s.strip() for s in args.symbol.split(",") if s.strip()]
    if not symbols:
        raise ValueError("未提供有效的指数代码")

    timeframe = getattr(args, "timeframe", "1d") or "1d"
    tf_label = "日线" if timeframe in ("1d", "日线") else timeframe
    buy, sell = _ma_pair(args)
    require_order = bool(getattr(args, "require_ma_order", False))
    sell_buffer = _sell_buffer(args)
    ma_desc = _ma_label(buy, sell, require_order, sell_buffer)
    print(f"开始回测：{', '.join(symbols)} | {tf_label} | {ma_desc} | {args.start} ~ {args.end or '最新'} | "
          f"成交={args.exec} | 佣金={args.commission*1e4:.1f}‱")

    panels = [run_one(s, args) for s in symbols]

    optimize_df = None
    if args.optimize:
        windows = parse_spec(args.optimize)
        n_periods = len(windows)
        print(f"参数寻优：在 {symbols[0]} 上扫描 买入×卖出 均线网格 "
              f"MA {windows.start}~{windows.stop-1} 步长 {windows.step}"
              f"（{n_periods}×{n_periods}={n_periods*n_periods} 个组合）...")
        ppy = _periods_per_year(timeframe, panels[0]["res"].daily_returns.index)
        optimize_df = optimize_ma(panels[0]["df"], windows, stats_start=args.start,
                                  commission=args.commission, slippage=args.slippage,
                                  exec_mode=args.exec, rf=args.rf, periods_per_year=ppy)

    # 报告元信息
    meta = {
        "exec_mode": args.exec, "commission": args.commission, "slippage": args.slippage,
        "ma_desc": ma_desc,
        "require_ma_order": require_order and buy < sell,
        "sell_buffer": sell_buffer,
        "start": panels[0]["res"].meta["start"], "end": panels[0]["res"].meta["end"],
    }
    tf_tag = "" if timeframe in ("1d", "日线") else f"{timeframe} · "
    if len(panels) == 1:
        p = panels[0]
        meta["title"] = f'{p["label"]} 均线择时回测报告'
        meta["subtitle"] = (f'{tf_tag}{ma_desc} · {meta["start"]} → {meta["end"]} · '
                            f'{"次日开盘" if args.exec=="next_open" else "当日收盘"}成交 · '
                            f'佣金 {args.commission*1e4:.1f}‱')
    else:
        meta["title"] = "多标的 均线择时回测对比"
        meta["subtitle"] = (f'{tf_tag}{ma_desc} · {meta["start"]} → {meta["end"]} · '
                            f'标的：{", ".join(p["label"] for p in panels)}')

    # 输出路径：save=False（图形界面）时不落盘，仅在内存生成 HTML
    save = getattr(args, "save", True)
    if getattr(args, "output", None):
        out = args.output
    elif save:
        ma_tag = f"MA{buy}" if buy == sell else f"MA{buy}-{sell}"
        out = (f'report_{symbols[0]}_{ma_tag}_{args.exec}.html' if len(panels) == 1
               else f'report_compare_{ma_tag}_{args.exec}.html')
    else:
        out = None

    html = render_html(panels, meta, out, optimize_df=optimize_df)
    if out:
        print(f"\n报告已生成：{out}")
    return SimpleNamespace(html=html, path=out)
