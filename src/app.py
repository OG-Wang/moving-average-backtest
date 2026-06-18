"""本地图形界面：用浏览器填参数运行回测。

启动后访问 http://127.0.0.1:5050 ，在网页表单里设置指数、均线、区间等参数，
点击"运行回测"即可在页面内查看报告。底层复用 core.execute()，与命令行同一套逻辑。

启动：  .venv\\Scripts\\python src\\app.py
或双击：start_ui.bat
"""

from __future__ import annotations

import os
import sys
import time
import traceback
import threading
import webbrowser
import math
from types import SimpleNamespace

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from collections import OrderedDict

from flask import Flask, request, jsonify, abort, Response, render_template_string

import core as backtest_core

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)

# 报告只存在内存中，不落盘——关闭本程序后即全部消失（数据缓存 CSV 仍保留）。
# 用 OrderedDict 做一个简单的 LRU，最多保留最近 _MAX_REPORTS 份，避免内存无限增长。
_REPORTS: "OrderedDict[str, str]" = OrderedDict()
_MAX_REPORTS = 8


def _store_report(rid: str, html: str) -> None:
    _REPORTS[rid] = html
    _REPORTS.move_to_end(rid)
    while len(_REPORTS) > _MAX_REPORTS:
        _REPORTS.popitem(last=False)  # 丢弃最旧的

# 常用指数快捷选择
PRESETS = [
    ("000688", "科创50"), ("000300", "沪深300"), ("000905", "中证500"),
    ("000001", "上证指数"), ("399006", "创业板指"), ("000852", "中证1000"),
    ("000016", "上证50"), ("899050", "北证50"), ("N225", "日经225"),
    ("HSTECH", "恒生科技"), ("HSI", "恒生指数"), ("SOXL", "美股"), ("TQQQ", "美股"),
]

PAGE = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>指数均线择时回测</title>
<style>
:root{ --ink:#1f2733; --muted:#6b7480; --line:#e6e9ee; --accent:#1f5fbf; --panel:#f7f8fa; }
*{box-sizing:border-box}
body{margin:0;background:#eef0f3;color:var(--ink);
 font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;}
.wrap{max-width:1180px;margin:0 auto;padding:24px 20px 60px;}
header.top{border-bottom:2px solid var(--accent);padding-bottom:12px;margin-bottom:18px;}
header.top h1{margin:0 0 3px;font-size:22px;}
header.top .meta{color:var(--muted);font-size:13px;}
.card{background:#fff;border:1px solid var(--line);border-radius:10px;padding:18px 20px;
 box-shadow:0 1px 2px rgba(20,30,50,.04);margin-bottom:18px;}
.card h2{margin:0 0 14px;font-size:15px;display:flex;align-items:center;gap:8px;}
.card h2::before{content:"";width:4px;height:14px;background:var(--accent);border-radius:2px;}
.presets{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:14px;}
.presets button{border:1px solid var(--line);background:var(--panel);color:var(--ink);
 padding:6px 12px;border-radius:16px;font-size:13px;cursor:pointer;}
.presets button:hover{border-color:var(--accent);color:var(--accent);}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px 16px;}
.field{display:flex;flex-direction:column;gap:5px;}
.field.col2{grid-column:span 2;}
.field label{font-size:12.5px;color:var(--muted);}
.field .hint{font-size:11px;color:#a3acb8;}
input,select{padding:8px 10px;border:1px solid var(--line);border-radius:7px;font-size:13.5px;
 color:var(--ink);background:#fff;width:100%;}
input:focus,select:focus{outline:none;border-color:var(--accent);}
.row-check{display:flex;align-items:center;gap:8px;font-size:13px;color:var(--muted);}
.row-check input{width:auto;}
.actions{display:flex;align-items:center;gap:14px;margin-top:18px;}
.btn{background:var(--accent);color:#fff;border:0;padding:10px 26px;border-radius:8px;
 font-size:14px;font-weight:600;cursor:pointer;}
.btn:disabled{opacity:.55;cursor:not-allowed;}
.status{font-size:13px;color:var(--muted);}
.status.err{color:#d83a34;}
.spinner{display:inline-block;width:14px;height:14px;border:2px solid #cdd6e2;
 border-top-color:var(--accent);border-radius:50%;animation:spin .8s linear infinite;vertical-align:-2px;margin-right:6px;}
@keyframes spin{to{transform:rotate(360deg)}}
#result{display:none;}
#result .bar{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;}
#result a{color:var(--accent);text-decoration:none;font-size:13px;}
/* 报告内嵌框：高度由 JS 按内容自适应，去边框/底色，使其与页面平铺为一体、不产生内部滚动条 */
iframe{width:100%;height:600px;border:0;background:transparent;display:block;}
@media(max-width:820px){.grid{grid-template-columns:repeat(2,1fr)}.field.col2{grid-column:span 2}}
</style></head>
<body><div class="wrap">
<header class="top">
  <h1>均线择时回测 · A股指数 / 全球指数 / 美股</h1>
  <div class="meta">收盘价上穿买入均线买入 · 跌破卖出均线卖出（可设不同周期）· 本地图形界面（数据源：新浪财经 / 东方财富）</div>
</header>

<div class="card">
  <h2>参数设置</h2>
  <div class="presets">
    {% for code,name in presets %}
    <button type="button" onclick="setSymbol('{{code}}')">{{name}} {{code}}</button>
    {% endfor %}
    <button type="button" onclick="addCompare(this)">+ 加入对比</button>
  </div>
  <form id="form">
    <div class="grid">
      <div class="field col2">
        <label>标的代码（多个用英文逗号分隔则对比）</label>
        <input name="symbol" id="symbol" value="000688" />
        <span class="hint">A股指数如 000688；全球/港股指数如 N225、HSTECH、HSI；美股如 SOXL、TQQQ；可混合对比 000688,N225,HSTECH</span>
      </div>
      <div class="field">
        <label>买入均线（上穿买入）</label>
        <input name="ma_buy" type="number" min="1" value="20" />
        <span class="hint">收盘价上穿该均线时买入，如 15</span>
      </div>
      <div class="field">
        <label>卖出均线（下穿卖出）</label>
        <input name="ma_sell" type="number" min="1" value="20" />
        <span class="hint">收盘价跌破该均线时卖出，如 20；与买入相同即单均线</span>
      </div>
      <div class="field col2">
        <label class="row-check"><input type="checkbox" name="require_ma_order" /> 顺势过滤：仅当买入均线高于卖出均线时才买入</label>
        <span class="hint">默认关闭。仅在「买入均线周期 &lt; 卖出均线周期」时有意义，可避免买入次日即被卖出的「一日游」</span>
      </div>
      <div class="field">
        <label>卖出缓冲（%）</label>
        <input name="sell_buffer" type="number" min="0" step="0.1" placeholder="留空=不启用" />
        <span class="hint">跌破卖出均线但在此范围内仍持有，如填 2 表示跌破超过 2% 才卖出；留空=跌破即卖</span>
      </div>
      <div class="field">
        <label>时间框架</label>
        <select name="timeframe">
          <option value="1d">日线（默认）</option>
          <option value="4h">4 小时（仅美股）</option>
          <option value="2h">2 小时（仅美股）</option>
          <option value="1h">1 小时（仅美股）</option>
          <option value="30m">30 分钟（仅美股）</option>
        </select>
        <span class="hint">日线支持 A股/全球/港股/美股；日内仅美股 · 数据源 Twelve Data</span>
      </div>
      <div class="field">
        <label>成交时点</label>
        <select name="exec">
          <option value="close">当日收盘价（默认）</option>
          <option value="next_open">次日开盘价（更贴近实盘）</option>
        </select>
      </div>
      <div class="field">
        <label>起始日期</label>
        <input name="start" type="date" value="2021-01-01" />
      </div>
      <div class="field">
        <label>结束日期（留空=最新）</label>
        <input name="end" type="date" />
      </div>
      <div class="field">
        <label>单边佣金率</label>
        <input name="commission" type="number" step="0.0001" value="0.0001" />
        <span class="hint">0.0001 = 万分之一，买卖各收一次</span>
      </div>
      <div class="field">
        <label>单边滑点率</label>
        <input name="slippage" type="number" step="0.0001" value="0" />
      </div>
      <div class="field">
        <label>无风险利率（年化）</label>
        <input name="rf" type="number" step="0.01" value="0" />
        <span class="hint">用于夏普/Sortino</span>
      </div>
      <div class="field">
        <label>MA 参数寻优（可选）</label>
        <input name="optimize" placeholder="如 5:120:5" />
        <span class="hint">起:止:步长，留空不启用</span>
      </div>
      <div class="field" style="justify-content:flex-end">
        <label class="row-check"><input type="checkbox" name="refresh" /> 强制刷新数据缓存</label>
      </div>
    </div>
    <div class="actions">
      <button class="btn" id="runBtn" type="submit">运行回测</button>
      <span class="status" id="status"></span>
    </div>
  </form>
</div>

<div class="card" id="result">
  <div class="bar">
    <h2 style="margin:0">回测报告</h2>
    <a id="openLink" href="#" target="_blank">在新标签打开 ↗</a>
  </div>
  <iframe id="frame" src="about:blank"></iframe>
</div>

</div>
<script>
let compareMode=false;
function setSymbol(code){
  const f=document.getElementById('symbol');
  if(compareMode){
    const parts=f.value.split(',').map(s=>s.trim()).filter(Boolean);
    if(!parts.includes(code)) parts.push(code);
    f.value=parts.join(',');
  }else{ f.value=code; }
}
function addCompare(btn){ compareMode=!compareMode;
  btn.textContent=compareMode?'✓ 对比模式(点指数追加)':'+ 加入对比'; }

// 将内嵌报告的高度同步为其真实内容高度，从而去掉框内滚动条、让报告随主页面一起滚动。
// 报告与本页同源（皆由本地 Flask 提供），可安全读取 contentDocument 测高。
let _frameResizeObs=null;
function fitFrame(){
  const f=document.getElementById('frame');
  const doc=f.contentDocument||f.contentWindow.document;
  if(!doc||!doc.body) return;
  // scrollHeight 取 body 与 documentElement 的较大者，覆盖不同报告结构
  const h=Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight);
  if(h>0) f.style.height=h+'px';
}
function bindFrameAutoResize(){
  const f=document.getElementById('frame');
  fitFrame();
  // Plotly 图表渲染/重排会改变高度，用 ResizeObserver 持续跟随；并加几次延迟兜底
  try{
    if(_frameResizeObs) _frameResizeObs.disconnect();
    const doc=f.contentDocument||f.contentWindow.document;
    if(window.ResizeObserver && doc && doc.body){
      _frameResizeObs=new ResizeObserver(()=>fitFrame());
      _frameResizeObs.observe(doc.body);
    }
  }catch(e){/* 跨源等异常时静默，退回固定高度 */}
  [200,600,1200,2500].forEach(t=>setTimeout(fitFrame,t));
}

document.getElementById('form').addEventListener('submit',async(e)=>{
  e.preventDefault();
  const btn=document.getElementById('runBtn'), st=document.getElementById('status');
  btn.disabled=true; st.className='status';
  st.innerHTML='<span class="spinner"></span>正在获取数据并回测，请稍候…';
  try{
    const data=new FormData(e.target);
    const r=await fetch('/run',{method:'POST',body:data});
    const j=await r.json();
    if(!j.ok){ throw new Error(j.error||'运行失败'); }
    st.textContent=j.summary||'完成';
    const res=document.getElementById('result');
    res.style.display='block';
    const url=j.url+'?t='+Date.now();
    const frame=document.getElementById('frame');
    frame.onload=bindFrameAutoResize;   // 报告加载完成后按内容高度自适应
    frame.src=url;
    document.getElementById('openLink').href=url;
    res.scrollIntoView({behavior:'smooth'});
  }catch(err){
    st.className='status err'; st.textContent='✗ '+err.message;
  }finally{ btn.disabled=false; }
});
// 主窗口尺寸变化时重新测高（Plotly 自适应宽度后高度可能变化）
window.addEventListener('resize',()=>{ if(document.getElementById('result').style.display!=='none') fitFrame(); });
</script>
</body></html>
"""


@app.route("/")
def index():
    return render_template_string(PAGE, presets=PRESETS)


def _parse_float(v, default, label: str) -> float:
    raw = "" if v is None else str(v).strip()
    if raw == "":
        return default
    try:
        x = float(raw)
    except ValueError as exc:
        raise ValueError(f"{label}必须是数字") from exc
    if not math.isfinite(x):
        raise ValueError(f"{label}必须是有限数字")
    return x


@app.route("/run", methods=["POST"])
def run():
    f = request.form
    try:
        symbol = (f.get("symbol") or "").strip()
        if not symbol:
            return jsonify(ok=False, error="请填写指数代码")
        ma_buy = int(f.get("ma_buy") or f.get("ma") or 20)
        ma_sell = int(f.get("ma_sell") or f.get("ma") or ma_buy)
        if ma_buy < 1 or ma_sell < 1:
            return jsonify(ok=False, error="均线周期必须 ≥ 1")
        optimize = (f.get("optimize") or "").strip() or None
        # 卖出缓冲：前端以百分数填写（如 2 表示 2%），留空=不启用；内部转为小数 0.02
        buf_raw = (f.get("sell_buffer") or "").strip()
        sell_buffer = 0.0
        if buf_raw:
            sell_buffer_pct = _parse_float(buf_raw, 0.0, "卖出缓冲比例")
            if sell_buffer_pct < 0 or sell_buffer_pct >= 100:
                return jsonify(ok=False, error="卖出缓冲比例必须在 0% 到小于 100% 之间")
            sell_buffer = sell_buffer_pct / 100.0
        rid = str(int(time.time() * 1000))
        commission = _parse_float(f.get("commission"), 0.0001, "单边佣金率")
        slippage = _parse_float(f.get("slippage"), 0.0, "单边滑点率")
        rf = _parse_float(f.get("rf"), 0.0, "无风险利率")
        if commission < 0:
            return jsonify(ok=False, error="单边佣金率必须 ≥ 0")
        if slippage < 0:
            return jsonify(ok=False, error="单边滑点率必须 ≥ 0")
        if commission + slippage >= 1:
            return jsonify(ok=False, error="单边佣金率 + 单边滑点率必须小于 100%")

        args = SimpleNamespace(
            symbol=symbol,
            ma=ma_buy,
            ma_buy=ma_buy,
            ma_sell=ma_sell,
            require_ma_order=bool(f.get("require_ma_order")),
            sell_buffer=sell_buffer,
            timeframe=(f.get("timeframe") or "1d").strip(),
            start=(f.get("start") or "2021-01-01").strip(),
            end=((f.get("end") or "").strip() or None),
            commission=commission,
            slippage=slippage,
            rf=rf,
            exec=(f.get("exec") or "close"),
            optimize=optimize,
            output=None,   # 不写文件
            save=False,    # 仅在内存生成 HTML
            refresh=bool(f.get("refresh")),
        )
        run_result = backtest_core.execute(args)
        _store_report(rid, run_result.html)   # 只存内存
        n = len([s for s in symbol.split(",") if s.strip()])
        tf = args.timeframe if args.timeframe not in ("1d", "日线") else "日线"
        ma_desc = f"MA{ma_buy}" if ma_buy == ma_sell else f"买MA{ma_buy}/卖MA{ma_sell}"
        if args.require_ma_order and ma_buy < ma_sell:
            ma_desc += "（顺势）"
        if sell_buffer > 0:
            ma_desc += f"（缓冲{sell_buffer*100:g}%）"
        summary = f"✓ 完成：{n} 个标的 · {tf} · {ma_desc} · 成交={args.exec}" + (f" · 寻优 {optimize}" if optimize else "")
        return jsonify(ok=True, url=f"/report/{rid}", summary=summary)
    except ValueError as e:
        return jsonify(ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        return jsonify(ok=False, error=f"{type(e).__name__}: {e}")


@app.route("/report/<rid>")
def report(rid):
    html = _REPORTS.get(rid)
    if html is None:
        abort(404, "报告已失效（仅在程序运行期间临时存于内存）")
    _REPORTS.move_to_end(rid)
    return Response(html, mimetype="text/html")


def _open_browser(port):
    time.sleep(1.0)
    webbrowser.open(f"http://127.0.0.1:{port}")


if __name__ == "__main__":
    port = int(os.environ.get("BACKTEST_PORT", "5050"))
    if os.environ.get("BACKTEST_NO_BROWSER") != "1":
        threading.Thread(target=lambda: _open_browser(port), daemon=True).start()
    print(f"图形界面已启动：http://127.0.0.1:{port}  （按 Ctrl+C 停止）")
    app.run(host="127.0.0.1", port=port, debug=False)
