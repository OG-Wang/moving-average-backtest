"""HTML 报告：用 Plotly 生成自包含、浅色专业版（券商研报风格）的回测报告。

对外主入口：render_html(panels, meta, output_path, optimize_df=None)
  - panels 长度为 1：完整单标的报告（指标卡片 + 权益曲线/买卖点 + 回撤 + 分年/月度 + 交易明细）
  - panels 长度 >1：多标的对比报告（并排指标 + 叠加权益曲线）
  - optimize_df 非空：追加 MA 参数寻优对比图与表
"""

from __future__ import annotations

import html as html_lib

import pandas as pd
import plotly.graph_objects as go
from plotly.io import to_html

from metrics import drawdown_series, yearly_returns, monthly_returns_table

# ---- 浅色专业版调色 ----
C_BG = "#ffffff"
C_PANEL = "#f6f8fb"
C_INK = "#18212c"
C_MUTED = "#4f5f70"
C_GRID = "#d9e0ea"
C_STRAT = "#174ea6"     # 策略线：沉稳蓝
C_BENCH = "#7a8796"     # 基准线：灰
C_BUY = "#c9332b"       # 买入：红（A股惯例 买红）
C_SELL = "#23804a"      # 卖出：绿（卖绿）
C_DD = "#c9332b"        # 回撤填充


def _esc(value) -> str:
    return html_lib.escape("" if value is None else str(value), quote=True)


_PLOT_LAYOUT = dict(
    paper_bgcolor=C_BG,
    plot_bgcolor=C_BG,
    font=dict(color=C_INK, family="-apple-system, 'Segoe UI', 'Microsoft YaHei', sans-serif", size=14),
    margin=dict(l=62, r=30, t=46, b=46),
    xaxis=dict(gridcolor=C_GRID, zeroline=False, tickfont=dict(size=12, color=C_MUTED),
               title_font=dict(size=13, color=C_MUTED)),
    yaxis=dict(gridcolor=C_GRID, zeroline=False, tickfont=dict(size=12, color=C_MUTED),
               title_font=dict(size=13, color=C_MUTED)),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                font=dict(size=13, color=C_MUTED)),
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
    label = _esc(panel["label"])
    eq, bh = res.equity, res.buy_hold
    # 每个交易日给策略线附加一条文本：仅在当日确有买/卖时显示成交价，其余日期为空。
    # 用 customdata 挂到策略线上，既能精确到日、又不会在非交易日带出旧值。
    extra = pd.Series("", index=eq.index, dtype=object)
    if len(res.trades):
        for _, tr in res.trades.iterrows():
            ed = pd.Timestamp(tr["entry_date"])
            if ed in extra.index:
                extra.loc[ed] = f"<br>▲ 买入价 {tr['entry_price']:.2f}"
            open_value = tr.get("is_open", False)
            is_open = False if pd.isna(open_value) else bool(open_value)
            if not is_open and pd.notna(tr["exit_date"]):
                xd = pd.Timestamp(tr["exit_date"])
                if xd in extra.index:
                    extra.loc[xd] = f"<br>▼ 卖出价 {tr['exit_price']:.2f}"

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name=f"{label} 策略",
                             line=dict(color=C_STRAT, width=2.6),
                             customdata=extra.values,
                             hovertemplate="策略净值 %{y:.3f}%{customdata}<extra></extra>"))
    fig.add_trace(go.Scatter(x=bh.index, y=bh.values, name="买入持有",
                             line=dict(color=C_BENCH, width=1.9, dash="dash"),
                             hovertemplate="买入持有 %{y:.3f}<extra></extra>"))
    if show_markers and len(res.trades):
        t = res.trades
        open_mask = (t["is_open"].fillna(False).astype(bool)
                     if "is_open" in t.columns else pd.Series(False, index=t.index))
        closed = t.loc[~open_mask]
        buy_x = pd.to_datetime(t["entry_date"])
        buy_y = eq.reindex(buy_x).values
        # 买卖三角仅作可视标注，不并入统一悬停框（成交价已通过策略线的 customdata 在交易日显示）
        fig.add_trace(go.Scatter(x=buy_x, y=buy_y, mode="markers", name="买入", hoverinfo="skip",
                                 marker=dict(symbol="triangle-up", size=11, color=C_BUY,
                                             line=dict(width=0.8, color="#fff"))))
        if len(closed):
            sell_x = pd.to_datetime(closed["exit_date"])
            sell_y = eq.reindex(sell_x).values
            fig.add_trace(go.Scatter(x=sell_x, y=sell_y, mode="markers", name="卖出", hoverinfo="skip",
                                     marker=dict(symbol="triangle-down", size=11, color=C_SELL,
                                                 line=dict(width=0.8, color="#fff"))))
    fig.update_layout(**_PLOT_LAYOUT, height=420)
    fig.update_yaxes(title_text="净值（起点=1.0）")
    fig.update_xaxes(hoverformat="%Y-%m-%d")  # 悬停日期精确到日
    return fig


def _multi_equity_fig(panels: list[dict]) -> go.Figure:
    palette = [C_STRAT, "#d83a34", "#2e9e5b", "#b8860b", "#7b3fa0", "#0e8a8a"]
    fig = go.Figure()
    for i, p in enumerate(panels):
        eq = p["res"].equity
        label = _esc(p["label"])
        fig.add_trace(go.Scatter(x=eq.index, y=eq.values, name=label,
                                 line=dict(color=palette[i % len(palette)], width=2.5),
                                 hovertemplate="净值 %{y:.3f}<extra></extra>"))
    fig.update_layout(**_PLOT_LAYOUT, height=460)
    fig.update_yaxes(title_text="净值（起点=1.0）")
    fig.update_xaxes(hoverformat="%Y-%m-%d")
    return fig


def _drawdown_fig(panel: dict) -> go.Figure:
    dd = drawdown_series(panel["res"].equity) * 100
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dd.index, y=dd.values, name="回撤", fill="tozeroy",
                             line=dict(color=C_DD, width=1.4),
                             fillcolor="rgba(201,51,43,0.16)",
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
        textfont=dict(size=12, color=C_INK),
        colorscale=[[0, C_SELL], [0.5, "#ffffff"], [1, C_BUY]],
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
                             mode="lines+markers", line=dict(color=C_BUY, width=2.4), yaxis="y2"))
    layout = {k: v for k, v in _PLOT_LAYOUT.items() if k not in ("yaxis",)}
    fig.update_layout(**layout, height=380,
                      yaxis=dict(title="夏普比率", gridcolor=C_GRID),
                      yaxis2=dict(title="总收益(%)", overlaying="y", side="right", showgrid=False))
    fig.update_xaxes(title_text="买入/卖出均线组合", tickangle=-45)
    return fig


# ---------- HTML 片段 ----------

def _metric_cards(m: dict) -> str:
    def pl_ratio(value: float) -> str:
        return "∞" if value == float("inf") else f"{value:.2f}"

    def trade_expectancy() -> float:
        win_rate = float(m["win_rate"])
        ratio = float(m["profit_loss_ratio"])
        if ratio == float("inf"):
            return float("inf") if win_rate > 0 else -(1 - win_rate)
        return win_rate * ratio - (1 - win_rate)

    def card(label, value, good=None, sub="", tip=""):
        cls = "" if good is None else (" pos" if good else " neg")
        sub_html = f'<div class="sub">{_esc(sub)}</div>' if sub else ""
        title_attr = f' title="{_esc(tip)}"' if tip else ""
        info = '<span class="info">ⓘ</span>' if tip else ""
        return (f'<div class="card{cls}"{title_attr}><div class="lbl">{_esc(label)}{info}</div>'
                f'<div class="val">{_esc(value)}</div>{sub_html}</div>')

    cards = [
        card("总收益率", _pct(m["total_return"]), m["total_return"] > 0,
             f'买入持有 {_pct(m["bh_total_return"])}'),
        card("年化收益率", _pct(m["annual_return"]), m["annual_return"] > 0,
             f'买入持有 {_pct(m["bh_annual_return"])}'),
        card("期间最高收益率", _pct(m["peak_return"]), m["peak_return"] > 0,
             f'买入持有 {_pct(m["bh_peak_return"])}',
             tip="回测期间策略净值曾达到的最高收益水平，即 max(净值) - 1；不同于最终总收益率。"),
        card("最大回撤", _pct(m["max_drawdown"]), m["max_drawdown"] >= -0.2,
             f'买入持有 {_pct(m["bh_max_drawdown"])}'),
        card("夏普比率", f'{m["sharpe"]:.2f}', m["sharpe"] > 0,
             sub="每单位风险的超额收益",
             tip="(年化收益率 − 无风险利率) ÷ 收益波动率。衡量每承担一单位“总波动风险”能换来多少超额收益，越高越好；>1 通常算不错。"),
        card("胜率 / 盈亏比", f'{_pct(m["win_rate"], 1)} / {pl_ratio(m["profit_loss_ratio"])}',
             trade_expectancy() > 0,
             sub="胜率 / 平均盈亏比",
             tip="按交易期望染色：胜率 × 盈亏比 − (1 − 胜率)。大于 0 标红，否则标绿。"),
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
        return f'<tr><td>{_esc(label)}</td>{cells}</tr>'

    head = "".join(f"<th>{_esc(y)}</th>" for y in years)
    return (f'<table class="yearly"><thead><tr><th>年份</th>{head}</tr></thead><tbody>'
            f'{row("策略", strat_yr)}{row("买入持有", bh_yr)}</tbody></table>')


def _trades_table(trades: pd.DataFrame) -> str:
    if not len(trades):
        return '<p class="muted">区间内无完整交易。</p>'
    rows = []
    for _, r in trades.iterrows():
        cls = "pos" if r["win"] else "neg"
        open_value = r.get("is_open", False)
        is_open = False if pd.isna(open_value) else bool(open_value)
        exit_date = "" if is_open or pd.isna(r["exit_date"]) else _esc(r["exit_date"])
        exit_price = "" if is_open or pd.isna(r["exit_price"]) else f'{r["exit_price"]:.2f}'
        ret = _pct(r["return"])
        if is_open:
            ret += '<div class="muted" style="font-size:11.5px">截止运行日的收益率</div>'
        rows.append(
            f'<tr><td>{_esc(r["entry_date"])}</td><td>{r["entry_price"]:.2f}</td>'
            f'<td>{exit_date}</td><td>{exit_price}</td>'
            f'<td>{r["holding_days"]}</td><td class="{cls}">{ret}</td></tr>'
        )
    return (
        '<div class="tbl-wrap"><table class="trades"><thead><tr>'
        '<th>买入日</th><th>买入价</th><th>卖出日</th><th>卖出价</th>'
        '<th>持仓天数</th><th>收益率</th></tr></thead><tbody>'
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
            f'<td>{_esc(r.get("reason",""))}</td></tr>'
        )
    return (
        '<div class="tbl-wrap"><table class="trades"><thead><tr>'
        '<th>买入/卖出均线</th><th>总收益</th><th>年化</th><th>最大回撤</th>'
        '<th>夏普</th><th>Calmar</th><th>交易数</th><th>胜率</th><th>入选</th>'
        '</tr></thead><tbody>' + "".join(rows) + "</tbody></table></div>"
    )


def _comparison_table(panels: list[dict]) -> str:
    def pl_ratio(value: float) -> str:
        return "∞" if value == float("inf") else f"{value:.2f}"

    head = "".join(f"<th>{_esc(p['label'])}</th>" for p in panels)
    metric_rows = [
        ("总收益率", lambda m: _pct(m["total_return"]), "total_return"),
        ("年化收益率", lambda m: _pct(m["annual_return"]), "annual_return"),
        ("期间最高收益率", lambda m: _pct(m["peak_return"]), "peak_return"),
        ("最大回撤", lambda m: _pct(m["max_drawdown"]), None),
        ("夏普比率", lambda m: f'{m["sharpe"]:.2f}', "sharpe"),
        ("Sortino", lambda m: f'{m["sortino"]:.2f}', "sortino"),
        ("Calmar", lambda m: f'{m["calmar"]:.2f}', "calmar"),
        ("交易数", lambda m: str(m["n_trades"]), None),
        ("胜率 / 盈亏比", lambda m: f'{_pct(m["win_rate"], 1)} / {pl_ratio(m["profit_loss_ratio"])}', None),
        ("平均持仓天数", lambda m: f'{m["avg_holding_days"]:.1f}', None),
        ("买入持有总收益", lambda m: _pct(m["bh_total_return"]), None),
        ("买入持有最高收益", lambda m: _pct(m["bh_peak_return"]), None),
    ]
    body = ""
    for label, fn, signkey in metric_rows:
        cells = ""
        for p in panels:
            v = fn(p["metrics"])
            cls = ""
            if signkey:
                cls = "pos" if p["metrics"][signkey] > 0 else "neg"
            cells += f'<td class="{cls}">{_esc(v)}</td>'
        body += f"<tr><td>{_esc(label)}</td>{cells}</tr>"
    return (f'<div class="tbl-wrap no-inner-scroll"><table class="trades cmp"><thead><tr><th>指标</th>{head}</tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


_CSS = """
* { box-sizing: border-box; letter-spacing:0; }
body { margin:0; background:#f1f4f8; color:#18212c; font-size:15px; line-height:1.5;
  font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;
  -webkit-font-smoothing:antialiased; text-rendering:optimizeLegibility; }
.wrap { max-width:1220px; margin:0 auto; padding:28px 24px 64px; }
header.top { background:#fff; border:1px solid #cfd7e2; border-top:4px solid #174ea6;
  border-radius:8px; padding:18px 22px; margin-bottom:18px; }
header.top h1 { margin:0 0 6px; font-size:25px; line-height:1.25; font-weight:750; }
header.top .meta { color:#4f5f70; font-size:14px; line-height:1.55; }
section { background:#fff; border:1px solid #cfd7e2; border-radius:8px;
  padding:22px; margin-top:18px; box-shadow:0 1px 2px rgba(19,33,54,.04); }
section h2 { margin:0 0 16px; font-size:17px; line-height:1.3; font-weight:700;
  display:flex; align-items:center; gap:9px; }
section h2::before { content:""; flex:0 0 auto; width:4px; height:16px; background:#174ea6; border-radius:2px; }
.cards { display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:14px; justify-items:stretch; }
.card { background:#f6f8fb; border:1px solid #d5dde8; border-radius:8px; padding:14px 16px;
  width:100%; min-height:98px; text-align:center; display:flex; flex-direction:column; justify-content:center; }
.card[title] { cursor:help; }
.card .lbl { font-size:13px; line-height:1.35; color:#4f5f70; font-weight:650; }
.card .lbl .info { font-size:11px; color:#748394; margin-left:3px; vertical-align:1px; }
.card .val { font-size:24px; line-height:1.2; font-weight:800; margin-top:5px; font-variant-numeric:tabular-nums; }
.card .sub { font-size:12.5px; line-height:1.35; color:#6f7d8c; margin-top:5px; }
.card.pos .val { color:#c9332b; }
.card.neg .val { color:#23804a; }
table { border-collapse:collapse; width:100%; font-size:14px; font-variant-numeric:tabular-nums; }
table th, table td { padding:9px 12px; text-align:right; border-bottom:1px solid #e1e6ee; vertical-align:middle; }
table th:first-child, table td:first-child { text-align:left; }
table thead th { background:#eef2f7; color:#303b48; font-weight:700; position:sticky; top:0; z-index:1; }
table tbody tr:hover { background:#f7f9fc; }
table.yearly td, table.yearly th { text-align:center; }
.tbl-wrap { max-height:460px; overflow:auto; border:1px solid #d5dde8; border-radius:8px; }
.tbl-wrap.no-inner-scroll { max-height:none; overflow:visible; }
table.cmp td, table.cmp th { text-align:center; }
table.cmp td:first-child, table.cmp th:first-child { text-align:left; font-weight:700; }
td.pos, .trades .pos { color:#c9332b; font-weight:650; }
td.neg, .trades .neg { color:#23804a; font-weight:650; }
.muted { color:#5f6d7c; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:18px; }
.note { font-size:13px; color:#5f6d7c; margin-top:26px; line-height:1.75;
  border-top:1px solid #cfd7e2; padding-top:16px; }
.copyright { text-align:center; color:#748394; font-size:12.5px; margin-top:18px; }
@media (max-width:980px){ .cards{grid-template-columns:repeat(3,minmax(0,1fr))} .grid2{grid-template-columns:1fr} }
@media (max-width:680px){
  .wrap{padding:18px 12px 44px}
  header.top,section{padding:16px}
  header.top h1{font-size:21px}
  .cards{grid-template-columns:repeat(2,minmax(0,1fr))}
  .card .val{font-size:22px}
  table{font-size:13.5px}
  table th,table td{padding:8px 9px}
}
@media (max-width:460px){ .cards{grid-template-columns:1fr} }
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
        open_trades = int(m.get("open_trades", 0))
        trade_title = (f'交易明细（{m["n_trades"]} 笔已平仓，{open_trades} 笔持仓中）'
                       if open_trades else f'交易明细（{m["n_trades"]} 笔）')
        body_parts.append(f'<section><h2>{trade_title}</h2>{_trades_table(p["res"].trades)}</section>')
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
    _buf = meta.get("sell_buffer") or 0
    buffer_note = (f'卖出设有缓冲：仅当收盘价跌破卖出均线超过 {_buf*100:g}% 才卖出，'
                   '其下方小幅波动继续持有。' if _buf > 0 else '')
    note = (
        '<div class="note">'
        f'策略：{_esc(meta.get("ma_desc","MA"))} —— 收盘价上穿买入均线当日买入、跌破卖出均线当日卖出（满仓/空仓择时）。'
        f'{order_note}'
        f'{buffer_note}'
        f'成交模式：{"次日开盘价" if meta.get("exec_mode")=="next_open" else "当日收盘价"}成交；'
        f'单边佣金 {meta.get("commission",0)*1e4:.1f}‱'
        + (f'，滑点 {meta.get("slippage",0)*1e4:.1f}‱' if meta.get("slippage") else "") + '。<br>'
        '说明：当日收盘价成交模式含轻微未来函数（以当日收盘价决策并成交），属日线回测常规简化；'
        '如需更贴近实盘可选「次日开盘」成交。部分指数不可直接交易，本结果仅为策略验证，不构成投资建议。<br>'
        f'数据源：akshare（新浪财经/东方财富等）/ Binance 现货公开接口。生成区间：{_esc(meta.get("start"))} ~ {_esc(meta.get("end"))}。'
        '</div>'
    )
    footer = '<div class="copyright">Copyright © Rick</div>'

    title = _esc(meta.get("title", "指数均线择时回测报告"))
    subtitle = _esc(meta.get("subtitle", ""))
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
