"""HTML 报告：用 Plotly 生成自包含、浅色专业版（券商研报风格）的回测报告。

对外主入口：render_html(panels, meta, output_path, optimize_df=None)
  - panels 长度为 1：完整单标的报告（指标卡片 + 权益曲线/买卖点 + 回撤 + 分年/月度 + 交易明细）
  - panels 长度 >1：多标的对比报告（并排指标 + 叠加权益曲线）
  - optimize_df 非空：追加 MA 参数寻优对比图与表
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.io import to_html

from metrics import drawdown_series, yearly_returns, monthly_returns_table

# ---- 浅色专业版调色 ----
C_BG = "#ffffff"
C_PANEL = "#f7f8fa"
C_INK = "#1f2733"
C_MUTED = "#6b7480"
C_GRID = "#e6e9ee"
C_STRAT = "#1f5fbf"     # 策略线：沉稳蓝
C_BENCH = "#9aa4b2"     # 基准线：灰
C_BUY = "#d83a34"       # 买入：红（A股惯例 买红）
C_SELL = "#2e9e5b"      # 卖出：绿（卖绿）
C_DD = "#d83a34"        # 回撤填充

_PLOT_LAYOUT = dict(
    paper_bgcolor=C_BG,
    plot_bgcolor=C_BG,
    font=dict(color=C_INK, family="-apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif", size=13),
    margin=dict(l=55, r=25, t=40, b=40),
    xaxis=dict(gridcolor=C_GRID, zeroline=False),
    yaxis=dict(gridcolor=C_GRID, zeroline=False),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    hovermode="x unified",
)


def _pct(x: float, digits: int = 2) -> str:
    return f"{x * 100:.{digits}f}%"


def _fig_to_div(fig: go.Figure, include_js: bool) -> str:
    return to_html(
        fig,
        include_plotlyjs=("inline" if include_js else False),
        full_html=False,
        config={"displaylogo": False, "responsive": True},
    )


# ---------- 各图表 ----------

def _equity_fig(panel: dict, show_markers: bool = True) -> go.Figure:
    res = panel["res"]
    eq, bh = res.equity, res.buy_hold
    # 每个交易日给策略线附加一条文本：仅在当日确有买/卖时显示成交价，其余日期为空。
    # 用 customdata 挂到策略线上，既能精确到日、又不会在非交易日带出旧值。
    extra = pd.Series("", index=eq.index, dtype=object)
    if len(res.trades):
        for _, tr in res.trades.iterrows():
            ed, xd = pd.Timestamp(tr["entry_date"]), pd.Timestamp(tr["exit_date"])
            if ed in extra.index:
                extra.loc[ed] = f"<br>▲ 买入价 {tr['entry_price']:.2f}"
            if xd in extra.index:
                extra.loc[xd] = f"<br>▼ 卖出价 {tr['exit_price']:.2f}"

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name=f"{panel['label']} 策略",
                             line=dict(color=C_STRAT, width=2),
                             customdata=extra.values,
                             hovertemplate="策略净值 %{y:.3f}%{customdata}<extra></extra>"))
    fig.add_trace(go.Scatter(x=bh.index, y=bh.values, name="买入持有",
                             line=dict(color=C_BENCH, width=1.5, dash="dash"),
                             hovertemplate="买入持有 %{y:.3f}<extra></extra>"))
    if show_markers and len(res.trades):
        t = res.trades
        buy_x = pd.to_datetime(t["entry_date"])
        sell_x = pd.to_datetime(t["exit_date"])
        buy_y = eq.reindex(buy_x).values
        sell_y = eq.reindex(sell_x).values
        # 买卖三角仅作可视标注，不并入统一悬停框（成交价已通过策略线的 customdata 在交易日显示）
        fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode="markers", name="买入", hoverinfo="skip",
                                 marker=dict(symbol="triangle-up", size=10, color=C_BUY,
                                             line=dict(width=0.5, color="#fff"))))
        fig.add_trace(go.Scatter(x=sell_x, y=sell_y, mode="markers", name="卖出", hoverinfo="skip",
                                 marker=dict(symbol="triangle-down", size=10, color=C_SELL,
                                             line=dict(width=0.5, color="#fff"))))
    fig.update_layout(**_PLOT_LAYOUT, height=420)
    fig.update_yaxes(title_text="净值（起点=1.0）")
    fig.update_xaxes(hoverformat="%Y-%m-%d")  # 悬停日期精确到日
    return fig


def _multi_equity_fig(panels: list[dict]) -> go.Figure:
    palette = [C_STRAT, "#d83a34", "#2e9e5b", "#b8860b", "#7b3fa0", "#0e8a8a"]
    fig = go.Figure()
    for i, p in enumerate(panels):
        eq = p["res"].equity
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name=p["label"],
                                 line=dict(color=palette[i % len(palette)], width=2),
                                 hovertemplate=f"{p['label']} %{{y:.3f}}<extra></extra>"))
    fig.update_layout(**_PLOT_LAYOUT, height=460)
    fig.update_yaxes(title_text="净值（起点=1.0）")
    fig.update_xaxes(hoverformat="%Y-%m-%d")
    return fig


def _drawdown_fig(panel: dict) -> go.Figure:
    dd = drawdown_series(panel["res"].equity) * 100
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, name="回撤", fill="tozeroy",
                             line=dict(color=C_DD, width=1),
                             fillcolor="rgba(216,58,52,0.15)",
                             hovertemplate="回撤 %{y:.2f}%<extra></extra>"))
    fig.update_layout(**_PLOT_LAYOUT, height=260)
    fig.update_yaxes(title_text="回撤 (%)", ticksuffix="")
    fig.update_xaxes(hoverformat="%Y-%m-%d")
    return fig


def _monthly_heatmap_fig(panel: dict) -> go.Figure:
    table = monthly_returns_table(panel["res"].daily_returns) * 100
    years = [str(y) for y in table.index]
    months = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"]
    z = table.values
    text = [[("" if pd.isna(v) else f"{v:.1f}") for v in row] for row in z]
    fig = go.Figure(go.Heatmap(
        z=z, x=months, y=years, text=text, texttemplate="%{text}",
        textfont=dict(size=11),
        colorscale=[[0, "#2e9e5b"], [0.5, "#ffffff"], [1, "#d83a34"]],
        zmid=0, colorbar=dict(title="%", ticksuffix="%"),
        hovertemplate="%{y}年%{x}: %{z:.2f}%<extra></extra>",
    ))
    fig.update_layout(**{k: v for k, v in _PLOT_LAYOUT.items() if k not in ("xaxis", "yaxis", "hovermode")},
                      height=60 + 34 * len(years))
    fig.update_yaxes(autorange="reversed")
    return fig


def _optimize_fig(opt: pd.DataFrame) -> go.Figure:
    # 入选组合（夏普Top20 ∪ 收益Top20）按夏普降序排成柱状；x 轴为「买x/卖y」组合标签
    o = opt.sort_values("sharpe", ascending=False)
    labels = [f'买{int(b)}/卖{int(s)}' for b, s in zip(o["ma_buy"], o["ma_sell"])]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=labels, y=o["sharpe"], name="夏普比率",
                         marker_color=C_STRAT, yaxis="y1"))
    fig.add_trace(go.Scatter(x=labels, y=o["total_return"] * 100, name="总收益(%)",
                             mode="lines+markers", line=dict(color=C_BUY, width=2), yaxis="y2"))
    layout = {k: v for k, v in _PLOT_LAYOUT.items() if k not in ("yaxis",)}
    fig.update_layout(**layout, height=380,
                      yaxis=dict(title="夏普比率", gridcolor=C_GRID),
                      yaxis2=dict(title="总收益(%)", overlaying="y", side="right", showgrid=False))
    fig.update_xaxes(title_text="买入/卖出均线组合", tickangle=-45)
    return fig


# ---------- HTML 片段 ----------

def _metric_cards(m: dict) -> str:
    def card(label, value, good=None, sub="", tip=""):
        cls = "" if good is None else (" pos" if good else " neg")
        sub_html = f'<div class="sub">{sub}</div>' if sub else ""
        title_attr = f' title="{tip}"' if tip else ""
        info = '<span class="info">ⓘ</span>' if tip else ""
        return (f'<div class="card{cls}"{title_attr}><div class="lbl">{label}{info}</div>'
                f'<div class="val">{value}</div>{sub_html}</div>')

    cards = [
        card("总收益率", _pct(m["total_return"]), m["total_return"] > 0,
             f'买入持有 {_pct(m["bh_total_return"])}'),
        card("年化收益率", _pct(m["annual_return"]), m["annual_return"] > 0,
             f'买入持有 {_pct(m["bh_annual_return"])}'),
        card("最大回撤", _pct(m["max_drawdown"]), m["max_drawdown"] >= -0.2,
             f'买入持有 {_pct(m["bh_max_drawdown"])}'),
        card("夏普比率", f'{m["sharpe"]:.2f}', m["sharpe"] > 0,
             sub="每单位风险的超额收益",
             tip="(年化收益率 − 无风险利率) ÷ 收益波动率。衡量每承担一单位“总波动风险”能换来多少超额收益，越高越好；>1 通常算不错。"),
        card("胜率", _pct(m["win_rate"], 1), m["win_rate"] >= 0.5),
        card("盈亏比", ("∞" if m["profit_loss_ratio"] == float("inf") else f'{m["profit_loss_ratio"]:.2f}'),
             m["profit_loss_ratio"] >= 1),
        card("Sortino", f'{m["sortino"]:.2f}', m["sortino"] > 0,
             sub="每单位下行风险收益",
             tip="索提诺比率。与夏普类似，但分母只统计“下行波动”（亏损方向的波动），不惩罚上涨波动，更贴近投资者对风险的真实感受，越高越好。"),
        card("Calmar", f'{m["calmar"]:.2f}', m["calmar"] > 0,
             sub="年化收益 ÷ 最大回撤",
             tip="卡玛比率 = 年化收益率 ÷ 最大回撤绝对值。衡量“用多大的回撤代价换取收益”，越高代表性价比越好。"),
        card("交易次数", str(m["n_trades"]), None, "回测期间完整买卖回合"),
        card("平均持仓天数", f'{m["avg_holding_days"]:.1f}', None,
             f'最长 {m["max_holding_days"]} / 最短 {m["min_holding_days"]} 天'),
    ]
    return '<div class="cards">' + "".join(cards) + "</div>"


def _yearly_table(res) -> str:
    """分年度收益表：策略 vs 买入持有 两行对比。"""
    strat_yr = yearly_returns(res.daily_returns)
    bh_yr = yearly_returns(res.buy_hold.pct_change().fillna(0.0))
    years = strat_yr.index  # 两者同区间，年份一致
    bh_yr = bh_yr.reindex(years)

    def row(label, yr):
        cells = "".join(
            f'<td class="{"pos" if v > 0 else "neg"}">{_pct(v)}</td>' for v in yr.values
        )
        return f'<tr><td>{label}</td>{cells}</tr>'

    head = "".join(f"<th>{y}</th>" for y in years)
    return (f'<table class="yearly"><thead><tr><th>年份</th>{head}</tr></thead><tbody>'
            f'{row("策略", strat_yr)}{row("买入持有", bh_yr)}</tbody></table>')


def _trades_table(trades: pd.DataFrame) -> str:
    if not len(trades):
        return '<p class="muted">区间内无完整交易。</p>'
    rows = []
    for _, r in trades.iterrows():
        cls = "pos" if r["win"] else "neg"
        rows.append(
            f'<tr><td>{r["entry_date"]}</td><td>{r["entry_price"]:.2f}</td>'
            f'<td>{r["exit_date"]}</td><td>{r["exit_price"]:.2f}</td>'
            f'<td>{r["holding_days"]}</td><td class="{cls}">{_pct(r["return"])}</td></tr>'
        )
    return (
        '<div class="tbl-wrap"><table class="trades"><thead><tr>'
        '<th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th>'
        '<th>持仓天数</th><th>本笔收益</th></tr></thead><tbody>'
        + "".join(rows) + "</tbody></table></div>"
    )


def _optimize_table(opt: pd.DataFrame) -> str:
    rows = []
    for i, r in opt.iterrows():
        star = " ★" if i == 0 else ""
        rows.append(
            f'<tr><td>买MA{int(r["ma_buy"])} / 卖MA{int(r["ma_sell"])}{star}</td>'
            f'<td class="{"pos" if r["total_return"]>0 else "neg"}">{_pct(r["total_return"])}</td>'
            f'<td>{_pct(r["annual_return"])}</td>'
            f'<td class="neg">{_pct(r["max_drawdown"])}</td>'
            f'<td>{r["sharpe"]:.2f}</td><td>{r["calmar"]:.2f}</td>'
            f'<td>{int(r["n_trades"])}</td><td>{_pct(r["win_rate"],1)}</td>'
            f'<td>{r.get("reason","")}</td></tr>'
        )
    return (
        '<div class="tbl-wrap"><table class="trades"><thead><tr>'
        '<th>买入/卖出均线</th><th>总收益</th><th>年化</th><th>最大回撤</th>'
        '<th>夏普</th><th>Calmar</th><th>交易数</th><th>胜率</th><th>入选</th>'
        '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
    )


def _comparison_table(panels: list[dict]) -> str:
    head = "".join(f"<th>{p['label']}</th>" for p in panels)
    metric_rows = [
        ("总收益率", lambda m: _pct(m["total_return"]), "total_return"),
        ("年化收益率", lambda m: _pct(m["annual_return"]), "annual_return"),
        ("最大回撤", lambda m: _pct(m["max_drawdown"]), None),
        ("夏普比率", lambda m: f'{m["sharpe"]:.2f}', "sharpe"),
        ("Sortino", lambda m: f'{m["sortino"]:.2f}', "sortino"),
        ("Calmar", lambda m: f'{m["calmar"]:.2f}', "calmar"),
        ("交易数", lambda m: str(m["n_trades"]), None),
        ("胜率", lambda m: _pct(m["win_rate"], 1), None),
        ("盈亏比", lambda m: ("∞" if m["profit_loss_ratio"] == float("inf") else f'{m["profit_loss_ratio"]:.2f}'), None),
        ("平均持仓天数", lambda m: f'{m["avg_holding_days"]:.1f}', None),
        ("买入持有总收益", lambda m: _pct(m["bh_total_return"]), None),
    ]
    body = ""
    for label, fn, signkey in metric_rows:
        cells = ""
        for p in panels:
            v = fn(p["metrics"])
            cls = ""
            if signkey:
                cls = "pos" if p["metrics"][signkey] > 0 else "neg"
            cells += f'<td class="{cls}">{v}</td>'
        body += f"<tr><td>{label}</td>{cells}</tr>"
    return (f'<div class="tbl-wrap"><table class="trades cmp"><thead><tr><th>指标</th>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


_CSS = """
* { box-sizing: border-box; }
body { margin:0; background:#eef0f3; color:#1f2733;
  font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif; }
.wrap { max-width:1080px; margin:0 auto; padding:28px 22px 60px; }
header.top { border-bottom:2px solid #1f5fbf; padding-bottom:14px; margin-bottom:8px; }
header.top h1 { margin:0 0 4px; font-size:23px; font-weight:700; }
header.top .meta { color:#6b7480; font-size:13.5px; }
section { background:#fff; border:1px solid #e6e9ee; border-radius:10px;
  padding:18px 20px; margin-top:18px; box-shadow:0 1px 2px rgba(20,30,50,.04); }
section h2 { margin:0 0 12px; font-size:16px; font-weight:650;
  display:flex; align-items:center; gap:8px; }
section h2::before { content:""; width:4px; height:15px; background:#1f5fbf; border-radius:2px; }
.cards { display:grid; grid-template-columns:repeat(5,1fr); gap:12px; justify-items:center; }
.card { background:#f7f8fa; border:1px solid #e6e9ee; border-radius:8px; padding:12px 14px;
  width:100%; text-align:center; }
.card[title] { cursor:help; }
.card .lbl { font-size:12.5px; color:#6b7480; }
.card .lbl .info { font-size:10px; color:#aeb6c2; margin-left:3px; vertical-align:1px; }
.card .val { font-size:21px; font-weight:700; margin-top:3px; }
.card .sub { font-size:11.5px; color:#94a0ad; margin-top:3px; }
.card.pos .val { color:#d83a34; }
.card.neg .val { color:#2e9e5b; }
table { border-collapse:collapse; width:100%; font-size:13px; }
table th, table td { padding:7px 10px; text-align:right; border-bottom:1px solid #eef0f3; }
table th:first-child, table td:first-child { text-align:left; }
table thead th { background:#f3f5f8; color:#54606e; font-weight:600; position:sticky; top:0; }
table.yearly td, table.yearly th { text-align:center; }
.tbl-wrap { max-height:420px; overflow:auto; border:1px solid #eef0f3; border-radius:8px; }
table.cmp td, table.cmp th { text-align:center; }
table.cmp td:first-child, table.cmp th:first-child { text-align:left; font-weight:600; }
td.pos, .pos { color:#d83a34; }
td.neg, .neg { color:#2e9e5b; }
.muted { color:#94a0ad; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
.note { font-size:12px; color:#94a0ad; margin-top:26px; line-height:1.7;
  border-top:1px solid #e6e9ee; padding-top:14px; }
.copyright { text-align:center; color:#aeb6c2; font-size:12px; margin-top:18px; letter-spacing:.3px; }
@media (max-width:760px){ .cards{grid-template-columns:repeat(2,1fr)} .grid2{grid-template-columns:1fr} }
"""


def render_html(panels: list[dict], meta: dict, output_path: str | None = None,
                optimize_df: pd.DataFrame | None = None) -> str:
    """生成报告 HTML 字符串并返回。

    output_path 非空时同时写入该文件；为 None 时只返回字符串、不落盘
    （图形界面用此模式，HTML 仅存于内存，关闭程序即消失）。
    """
    figs_html = []          # 待嵌入的图表 div
    include_js = [True]     # 仅第一个图内联 plotly.js

    def emit(fig) -> str:
        html = _fig_to_div(fig, include_js[0])
        include_js[0] = False
        return html

    multi = len(panels) > 1
    body_parts = []

    if not multi:
        p = panels[0]
        m = p["metrics"]
        body_parts.append(f'<section><h2>关键绩效指标</h2>{_metric_cards(m)}</section>')
        body_parts.append(f'<section><h2>净值曲线（策略 vs 买入持有）</h2>{emit(_equity_fig(p))}</section>')
        body_parts.append(
            '<section><h2>回撤与分年度收益</h2>'
            f'{emit(_drawdown_fig(p))}'
            f'<div style="margin-top:14px">{_yearly_table(p["res"])}</div></section>'
        )
        body_parts.append(f'<section><h2>月度收益热力图</h2>{emit(_monthly_heatmap_fig(p))}</section>')
        body_parts.append(f'<section><h2>交易明细（{m["n_trades"]} 笔）</h2>{_trades_table(p["res"].trades)}</section>')
    else:
        body_parts.append(f'<section><h2>绩效对比</h2>{_comparison_table(panels)}</section>')
        body_parts.append(f'<section><h2>净值曲线对比</h2>{emit(_multi_equity_fig(panels))}</section>')

    if optimize_df is not None and len(optimize_df):
        best = optimize_df.iloc[0]
        body_parts.append(
            '<section><h2>均线组合参数寻优</h2>'
            f'<p class="muted">在 买入×卖出 均线网格上全扫，展示「夏普比率 Top20」∪「总收益率 Top20」'
            f'的并集去重（共 {len(optimize_df)} 个组合，按夏普降序）。'
            f'夏普最优为 <b>买MA{int(best["ma_buy"])} / 卖MA{int(best["ma_sell"])}</b>'
            f'（夏普 {best["sharpe"]:.2f}，总收益 {_pct(best["total_return"])}）。</p>'
            f'{emit(_optimize_fig(optimize_df))}'
            f'<div style="margin-top:14px">{_optimize_table(optimize_df)}</div></section>'
        )

    order_note = ('买入额外要求「买入均线 > 卖出均线」（顺势过滤），避免买入次日即触发卖出。'
                  if meta.get("require_ma_order") else '')
    note = (
        '<div class="note">'
        f'策略：{meta.get("ma_desc","MA")} —— 收盘价上穿买入均线当日买入、跌破卖出均线当日卖出（满仓/空仓择时）。'
        f'{order_note}'
        f'成交模式：{"次日开盘价" if meta.get("exec_mode")=="next_open" else "当日收盘价"}成交；'
        f'单边佣金 {meta.get("commission",0)*1e4:.1f}‱'
        + (f'，滑点 {meta.get("slippage",0)*1e4:.1f}‱' if meta.get("slippage") else "") + '。<br>'
        '说明：当日收盘价成交模式含轻微未来函数（以当日收盘价决策并成交），属日线回测常规简化；'
        '如需更贴近实盘可选「次日开盘」成交。指数不可直接交易，本结果仅为策略验证，不构成投资建议。<br>'
        f'数据源：新浪财经（akshare）。生成区间：{meta.get("start")} ~ {meta.get("end")}。'
        '</div>'
    )
    footer = '<div class="copyright">Copyright © Rick</div>'

    title = meta.get("title", "指数均线择时回测报告")
    subtitle = meta.get("subtitle", "")
    html = (
        f'<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<title>{title}</title><style>{_CSS}</style></head><body><div class="wrap">'
        f'<header class="top"><h1>{title}</h1><div class="meta">{subtitle}</div></header>'
        + "".join(body_parts) + note + footer +
        '</div></body></html>'
    )

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
    return html
