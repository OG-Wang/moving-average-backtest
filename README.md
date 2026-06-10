# 均线择时回测系统 · A股指数 / 美股

一个简单、可复用的均线择时回测工具。支持 **A 股指数**与**美股**，可回测**日线**及**美股日内**（4h/2h/1h/30m）框架。当收盘价上穿买入均线时满仓买入，跌破卖出均线时清仓卖出（买入、卖出可用不同周期的均线），输出完整绩效指标与交互式 HTML 报告。

## 策略逻辑

- 计算收盘价的买入均线 `MA(买入周期)` 与卖出均线 `MA(卖出周期)`（两者可相同，相同即退化为单均线策略）。
- **买入**：未持仓且 `收盘价 > 买入均线` → 建仓（满仓）。
- **卖出**：持仓中且 `收盘价 < 卖出均线`（向下穿越）→ 清仓。
- 价格落在两条均线之间、或恰等于均线时维持原持仓，避免抖动。
- 回测区间末若仍持仓，按最后一根 bar 收盘价强制平仓结算。

## 运行环境

程序代码跨平台（Python + Flask）。但仓库自带的 `start_ui.bat` 与 `.venv/` **仅适用于 Windows**——
`.bat` 在 macOS/Linux 上无法执行，`.venv/` 是 Windows 二进制、不可跨平台搬运。

### Windows

双击 **`start_ui.bat`** 即可（或 `.venv\Scripts\python src\app.py`）。浏览器自动打开 `http://127.0.0.1:5000`。

首次或缺依赖时安装：

```bat
.venv\Scripts\python -m pip install -r requirements.txt
```

### macOS / Linux

**方式 A：一键启动（自建 `start_ui.command`，类似 Windows 的 `.bat`）**

在项目根目录新建文件 `start_ui.command`，内容如下：

```bash
#!/bin/bash
cd "$(dirname "$0")"                     # 切到脚本所在目录
if [ ! -d ".venv" ]; then                # 首次运行：自动建 venv 并装依赖
  python3 -m venv .venv
  ./.venv/bin/pip install -r requirements.txt
fi
./.venv/bin/python src/app.py            # 启动，浏览器开 http://127.0.0.1:5000
```

然后给它执行权限（只需一次）：

```bash
chmod +x start_ui.command
```

之后**双击 `start_ui.command`** 即可一键启动（首次会自动建环境、装依赖，稍慢；之后秒开）。

> 若双击被 macOS 安全拦截，右键 →「打开」→ 确认一次即可；或在「系统设置 → 隐私与安全性」里允许。
> `start_ui.command` 是各人本地自建的，已被 `.gitignore` 忽略（`*.command`），不会进仓库。

**方式 B：手动启动**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python src/app.py            # 浏览器打开 http://127.0.0.1:5000
```

之后每次启动：`source .venv/bin/activate && python src/app.py`。

关闭命令行窗口即停止服务。

> 报告**只存在内存中、不写入磁盘**，关闭程序后即自动消失——本地只保留数据缓存（`data_cache/`），随时可重新计算。若需保存某份报告，在浏览器里「另存为」即可。

## 美股日内框架需要 API key

A 股 / 美股的**日线**回测无需任何 key。**美股日内**（4h/2h/1h/30m）使用 [Twelve Data](https://twelvedata.com)，需一个免费 key：

1. 注册并获取：<https://twelvedata.com/account/api-keys>（免费档 800 次/天、8 次/分）
2. 复制 `src/.env.example` 为 `src/.env`，填入：
   ```
   TWELVE_DATA_API_KEY=你的key
   ```

> `src/.env` 已被 `.gitignore` 忽略，不会提交。**切勿把含真实 key 的 `.env` 上传到 GitHub。**

## 使用：图形界面

在网页表单里设置参数，点「运行回测」即可在页面内查看交互式报告：

- **标的代码**：A 股指数如 `000688`；美股如 `SOXL`、`TQQQ`；逗号分隔可多标的并排对比（支持中美混合，如 `000688,SOXL`）。顶部有常用标的快捷按钮。
- **时间框架**：日线（默认，A股/美股皆可）/ 4h / 2h / 1h / 30m（**日内仅美股**）。
- **买入均线 / 卖出均线 / 起止日期 / 成交时点**（当日收盘 or 次日开盘）/ **佣金 / 滑点 / 无风险利率**。
- **均线组合参数寻优**：填 `START:END:STEP`（如 `5:120:5`）在「买入×卖出」均线网格上全扫（K 个周期 → K×K 个组合）；结果展示「夏普 Top20」∪「总收益 Top20」的并集去重（20~40 行），每行标注入选原因。
- **强制刷新数据缓存**。

## 输出指标

总收益率、年化收益率、最大回撤、夏普比率、Sortino、Calmar、年化波动率、
总交易数、胜率、盈亏比、平均/最大/最小持仓周期，并与买入持有基准对比；
另含分年度收益表、月度收益热力图、逐笔交易明细。

> 年化/夏普会按所选时间框架自动折算（日线按 252 交易日；日内按数据实际每年 bar 数推算，如 4h≈504）。

## 代码结构（便于复用）

| 文件 | 职责 |
|------|------|
| `src/data.py` | 取数：A股指数+美股**日线**（新浪为主、腾讯备用）；美股**日内**（Twelve Data）。重试 + CSV 缓存 + 均线预热；统一入口 `fetch_bars(symbol, timeframe, ...)` |
| `src/strategy.py` | 策略接口 `Strategy` + `MovingAverageStrategy`（输出 0/1 持仓信号） |
| `src/engine.py` | 回测引擎：信号→权益曲线 + 交易明细，支持两种成交时点（逐 bar，与周期无关） |
| `src/metrics.py` | 由权益/交易计算全部绩效指标（年化因子 `periods_per_year` 可配） |
| `src/optimize.py` | MA 参数寻优 |
| `src/report.py` | Plotly 自包含 HTML 报告 |
| `src/core.py` | 回测编排核心 `execute(args)`，串联各模块 |
| `src/app.py` | 本地图形界面（Flask），复用 `core.execute` |
| `start_ui.bat` | Windows 一键启动（macOS/Linux 见上方「运行环境」） |

新增其它策略：继承 `strategy.Strategy` 实现 `generate_signals`，引擎与指标无需改动。

## 数据源与假设说明

- **日线**：新浪财经（`akshare` 的 `stock_zh_index_daily` / `stock_us_daily`），免费、无需 token，缓存到 `data_cache/`。
- **美股日内**：Twelve Data（需免费 key，见上）。原生 4h/2h/1h/30m，免费档约 6~7 年历史，缓存到 `data_cache/us_{代码}_{周期}.csv` 并随运行增量增长。**日内仅支持美股**；A 股仅日线。
- 程序默认绕过系统代理（数据站点为公网）；若你的网络必须走代理，设环境变量 `BACKTEST_USE_PROXY=1`。
- 成交时点选「当日收盘」（默认）含轻微未来函数（以当日收盘价决策并成交），属常规简化；追求严谨可选「次日开盘」。
- 杠杆 ETF（如 SOXL）波动剧烈，回测结果对参数与时间框架高度敏感，**仅用于策略验证，不构成投资建议**。
