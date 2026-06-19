# AI Agent Reference for Backtest System

本文档帮助 AI 助手快速理解均线择时回测系统的架构、设计模式和实现细节，以便高效完成开发任务。

## Project Overview

**核心功能**：均线择时策略回测，支持 A股/全球/港股指数/美股日线，美股日内(4h/2h/1h/30m)，以及 BTC/ETH/BNB 的 Binance Spot 现货日线与日内(4h/2h/1h/30m)

**技术栈**：Python 3.9+, Flask, Pandas, NumPy, Plotly

**入口点**：
- CLI: `python src/core.py` (直接运行 execute 函数)
- Web UI: `python src/app.py` → Flask 服务

---

## Architecture

### 模块依赖图

```
┌─────────────────────────────────────────────────────────┐
│                    src/app.py (Flask UI)                 │
│  表单参数解析 → SimpleNamespace → core.execute(args)    │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────┐
│                   src/core.py (编排)                      │
│  execute(args) → run_one() → 串联各模块 → render_html() │
└───────┬────────────┬────────────┬────────────┬──────────┘
        │            │            │            │
┌───────▼───┐  ┌────▼──────┐  ┌──▼───────┐  ┌▼──────────┐
│src/data.py│  │src/strategy│ │src/engine│ │src/metrics│
│数据获取   │  │信号生成   │  │回测执行  │  │绩效计算  │
└───────────┘  └───────────┘  └──────────┘  └───────────┘
                                      │
                              ┌───────▼────────┐
                              │src/optimize.py │
                              │参数网格寻优    │
                              └────────────────┘
```

### 数据流

```
fetch_bars(symbol, timeframe, ...)
    → pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])
    → strategy.generate_signals(df)
    → pd.Series(dtype=int) # 0=空仓, 1=持仓
    → engine.run(df, signals)
    → SimpleNamespace(equity, daily_returns, trades, buy_hold, meta)
    → compute_metrics(...)
    → dict{total_return, peak_return, sharpe, max_drawdown, ...}
    → render_html(...)
    → str (HTML)
```

---

## Module Contract Specifications

### src/data.py - Data Layer

**核心函数**：
```python
def fetch_bars(symbol: str, timeframe: str, start: str | None = None,
               end: str | None = None, warmup: int = 0, refresh: bool = False
               ) -> pd.DataFrame:
    """
    返回标准化 DataFrame，索引为 datetime，列名小写。

    数据源路由：
    - A股指数日线: stock_zh_index_daily (akshare, 新浪)
    - 美股日线: stock_us_daily (akshare, 新浪)
    - 全球指数: index_global_hist_sina (新浪, 东方财富兜底)
    - 港股指数: stock_hk_index_daily_sina (新浪, 东方财富兜底)
    - 美股日内: TwelveData API (需 TWELVE_DATA_API_KEY)
    - 加密货币现货: Binance Spot klines (免费、无需 key，仅 BTC/ETH/BNB 与对应 USDT 交易对)

    缓存位置：
    - 日线股票/指数: data_cache/{cache_key}_daily.csv
    - 美股日内: data_cache/us_{SYM}_{timeframe}.csv
    - 加密货币: data_cache/crypto_{PAIR}_{daily|timeframe}.csv
    缓存逻辑：优先读本地 CSV；缺更早数据、不够新或 refresh=True 时联网更新并合并去重
    预热：warmup 额外获取数据以支持均线计算（不进入回测结果）

    返回数据至少包含：date(索引), open, high, low, close, volume
    """
```

**重要常量**：
- `CACHE_DIR = "data_cache/"`
- `_is_us(symbol)` → bool: 判断是否美股
- `_is_crypto(symbol)` → bool: 判断是否为内置支持的加密货币现货（BTC/ETH/BNB）
- `daily_symbol_label(symbol)` → str | None: 特殊指数/加密货币显示名称映射
- `TIMEFRAME_TO_TD` → 美股日内时间框架到 Twelve Data interval 的映射
- `TIMEFRAME_TO_BINANCE` → 加密货币时间框架到 Binance interval 的映射
- `BINANCE_SPOT_KLINES_URLS` → Binance Spot 公开 K 线入口，优先 data-api.binance.vision，回退 api.binance.com

**扩展新数据源**：
1. 实现返回标准 DataFrame 的函数
2. 在 `fetch_bars` 中添加路由判断
3. 参考 `index_global_hist_sina` 或 `fetch_crypto_bars` 的重试+缓存模式
4. 若扩展加密货币，先确认用户是否要扩大白名单；当前设计只支持 BTC/ETH/BNB，不自动接受任意 USDT 交易对

**加密货币约定**：
- 只使用 Binance Spot 现货 K 线：`BTCUSDT`、`ETHUSDT`、`BNBUSDT`
- 不使用 Binance USD-M futures 路径（`data/futures/um/...`）或 `fapi.binance.com`
- Binance 默认按 UTC+0 切 K 线；代码用 UTC open time 转成无时区 datetime 索引。日线 `2024-01-01` 对应北京时间 `2024-01-01 08:00` 到 `2024-01-02 07:59:59.999`

---

### src/strategy.py - Strategy Layer

**基类契约**：
```python
class Strategy(ABC):
    name: str = "Strategy"  # 用于报告显示

    @abstractmethod
    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """
        输入: df with columns including 'close', index is datetime
        输出: pd.Series(dtype=int, index=df.index), 值为 0 或 1
               0 表示该 bar 结束后应空仓
               1 表示该 bar 结束后应持仓

        策略不应关心成交价、成本，只输出持仓状态
        """
```

**MovingAverageStrategy 实现细节**：
```python
class MovingAverageStrategy(Strategy):
    def __init__(self, buy_window: int, sell_window: int | None = None,
                 require_ma_order: bool = False, sell_buffer: float = 0.0):
        """
        参数说明：
        - buy_window: 买入均线周期，必须 >= 1
        - sell_window: 卖出均线周期，None 则等于 buy_window
        - require_ma_order: 顺势过滤，仅当 buy_ma > sell_ma 时买入
                            (仅在 buy_window < sell_window 时有效)
        - sell_buffer: 卖出缓冲比例，0 表示跌破即卖，0.02 表示跌破 2% 才卖

        状态机逻辑：
        - held=False (空仓): if close > ma_buy and (not require_ma_order or ma_buy > ma_sell)
                              then held=True
        - held=True (持仓):  if close < ma_sell * (1 - sell_buffer)
                              then held=False
        - 其他情况: 维持 held 状态
        - 任一均线为 NaN: 强制 held=False
        """
```

**扩展新策略**：
1. 继承 `Strategy`
2. 实现 `generate_signals(df)` 返回 0/1 Series
3. 在 `__init__` 设置 `self.name` 用于显示
4. 策略名会出现在报告标题和控制台输出

---

### src/engine.py - Backtest Engine

**核心函数**：
```python
class BacktestEngine:
    def __init__(self, commission: float = 0.0001, slippage: float = 0.0,
                 exec_mode: str = "close"):
        """
        commission: 单边佣金率 (小数，如 0.0001 = 万分之一)
        slippage: 单边滑点率 (小数)
        exec_mode: 成交时点
            - "close": 当日收盘价成交 (默认)
            - "next_open": 次日开盘价成交
        """

    def run(self, df: pd.DataFrame, signals: pd.Series, stats_start: str | None = None,
            ma: pd.Series | None = None
            ) -> SimpleNamespace:
        """
        参数：
        - df: OHLCV 数据，必须有 'open' 和 'close' 列
        - signals: 0/1 序列，索引与 df 一致
        - stats_start: 统计起始日期，之前的数据仅用于信号生成
        - ma: 均线数据，用于报告绘制（可选）

        返回 SimpleNamespace:
            - equity: pd.Series (权益曲线，从 1.0 开始)
            - daily_returns: pd.Series (每日收益率)
            - trades: pd.DataFrame (交易明细，列: date, price, shares, equity_after, action)
            - buy_hold: pd.Series (买入持有基准权益)
            - meta: dict {start, end, n_trades, ...}
        """
```

**关键逻辑**：
- 持仓变化时触发交易：`if signal[i] != signal[i-1]`
- 成交价计算：
  - exec_mode="close": `df['close'][i]`
  - exec_mode="next_open": `df['open'][i+1]` (需检查边界)
- 滑点应用：买入 `price * (1 + slippage)`, 卖出 `price * (1 - slippage)`
- 佣金应用：交易金额 * commission

---

### src/metrics.py - Metrics Layer

```python
def compute_metrics(equity: pd.Series, daily_returns: pd.Series,
                    trades: pd.DataFrame, buy_hold: pd.Series,
                    rf: float = 0.0, periods_per_year: float = 252.0
                    ) -> dict:
    """
    返回字典包含以下键：
    - total_return: 总收益率
    - peak_return: 期间最高收益率 = max(equity) - 1
    - annual_return: 年化收益率
    - max_drawdown: 最大回撤 (负值)
    - sharpe: 夏普比率
    - sortino: Sortino 比率
    - calmar: Calmar 比率
    - n_trades: 总交易数
    - open_trades: 仍在持仓中的交易数
    - win_rate: 胜率
    - profit_loss_ratio: 盈亏比 = 平均盈利 / 平均亏损绝对值
    - avg_holding_days: 平均持仓天数
    - max_holding_days: 最大持仓天数
    - min_holding_days: 最小持仓天数
    - bh_total_return: 买入持有总收益率
    - bh_peak_return: 买入持有期间最高收益率
    - bh_annual_return: 买入持有年化收益率
    - bh_max_drawdown: 买入持有最大回撤
    """
```

**核心公式**：
- 年化收益率：`(1 + total_return) ** (periods_per_year / n_bars) - 1`
- 期间最高收益率：`equity.max() - 1`
- 夏普比率：`mean(daily_returns - rf / periods_per_year) / std(daily_returns) * sqrt(periods_per_year)`
- Sortino 比率：类似，分母只计算下行波动

---

### src/optimize.py - Parameter Optimization

```python
def parse_spec(spec: str) -> range:
    """
    解析 "5:120:5" 格式为 range(5, 120, 5)
    用于网格参数寻优
    """

def optimize_ma(df: pd.DataFrame, windows: range, stats_start: str | None = None,
                commission: float = 0.0001, slippage: float = 0.0,
                exec_mode: str = "close", rf: float = 0.0,
                periods_per_year: float = 252.0
                ) -> pd.DataFrame:
    """
    对所有 (buy_window, sell_window) 组合进行回测
    返回 DataFrame 包含：buy_ma, sell_ma, sharpe, total_return, max_drawdown, ...
    """
```

---

### src/core.py - Orchestration Layer

```python
def execute(args) -> SimpleNamespace:
    """
    args 必需属性 (SimpleNamespace 或命名空间对象):
        - symbol: str (逗号分隔多个标的)
        - ma: int (旧兼容) 或 ma_buy: int, ma_sell: int
        - start: str (YYYY-MM-DD)
        - end: str | None
        - commission: float
        - slippage: float
        - rf: float
        - exec: str ("close" or "next_open")
        - optimize: str | None ("START:END:STEP")
        - refresh: bool
        可选属性:
        - require_ma_order: bool
        - sell_buffer: float
        - timeframe: str (默认 "1d")
        - output: str | None (HTML 文件路径)
        - save: bool (是否写入文件，True 为 command line 模式，False 为 UI 模式)

    返回: SimpleNamespace(html=报告HTML字符串, path=文件路径或None)
    """
```

**内部辅助函数**：
- `_ma_pair(args)` → (buy, sell)
- `_sell_buffer(args)` → float
- `_periods_per_year(timeframe, index, symbol=None)` → float；股票/指数日线固定 252，加密货币与日内按实际 bar 密度推算
- `run_one(symbol, args)` → dict (单标的回测结果)

---

### src/app.py - Flask Web UI

**路由**：
```
GET  /                → 首页表单
POST /run             → 执行回测，返回 JSON {ok, url, summary, error?}
GET  /report/<rid>    → 获取报告 HTML
```

**内存管理**：
- 报告存储在 `_REPORTS` OrderedDict (LRU，最多 8 份)
- 关闭程序后所有报告消失，数据缓存保留

**配置**：
- 端口：`BACKTEST_PORT` 环境变量 (默认 5050)
- 自动打开浏览器：`BACKTEST_NO_BROWSER=1` 禁用

---

### src/report.py - Report Layer

**关键约定**：
- 单标的报告的关键指标区保持 10 张卡片（桌面端 5×2）。
- 收益类卡片包含：总收益率、年化收益率、期间最高收益率、最大回撤。
- `期间最高收益率` 展示策略 `peak_return`，sub 文案展示买入持有 `bh_peak_return`，形式与总收益/年化收益一致。
- `胜率` 与 `盈亏比` 合并为一张 `胜率 / 盈亏比` 卡片，显示如 `55.0% / 1.40`，用于维持 10 卡片布局。
- 多标的对比表也应包含 `期间最高收益率` 和 `买入持有最高收益`，避免单标的/多标的信息口径不一致。

---

## Common Task Patterns

### Pattern 1: 添加新策略

```python
# 1. 在 strategy.py 中定义新策略类
class RSIReversalStrategy(Strategy):
    name = "RSI反转"

    def __init__(self, period: int = 14, oversold: float = 30, overbought: float = 70):
        self.period = period
        self.oversold = oversold
        self.overbought = overbought

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        # 计算 RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(self.period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(self.period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        # 生成信号
        signals = pd.Series(0, index=df.index, dtype=int)
        signals[rsi < self.oversold] = 1  # 超卖买入
        signals[rsi > self.overbought] = 0  # 超买卖出
        return signals

# 2. 在 app.py 中添加表单字段
# 3. 在 core.execute() 中根据策略类型实例化
```

### Pattern 2: 添加新数据源

```python
# 在 data.py 中实现
def _fetch_my_source(symbol: str) -> pd.DataFrame:
    """自定义数据源实现"""
    # 1. 获取数据
    # 2. 标准化为 DataFrame: columns=['date','open','high','low','close','volume']
    # 3. date 列转为 datetime 索引
    # 4. 确保按日期升序
    # 5. 去重
    return df

# 在 fetch_bars() 中添加路由
if _is_my_source(symbol):
    return _fetch_my_source(symbol)
```

### Pattern 3: 扩展报告指标

```python
# 1. 在 metrics.py 中 compute_metrics() 添加计算
metrics['my_new_metric'] = my_calculation(equity, trades)

# 2. 在 report.py 中添加显示
# 单标的关键指标卡片在 _metric_cards()；多标的对比表在 _comparison_table()
# 注意单标的关键指标区当前约定为 10 张卡片，新增卡片时需要合并/替换相关指标以保持 5×2 布局

# 3. 考虑是否需要同步 README.md / Agent.md 的指标契约
# 4. 考虑是否需要在交易明细中添加列
```

---

## Key Design Decisions (Why Things Are This Way)

### 1. 分离信号生成与回测执行
**原因**：策略只输出 0/1，不关心成交价、成本。这样：
- 新增策略只需实现 `generate_signals()`
- 引擎和指标模块无需修改
- 便于单元测试

### 2. 数据缓存机制
**原因**：
- 免费数据源有请求限制和延迟
- 回测结果可复现
- 缓存文件命名规则：
  - 日线股票/指数：`data_cache/{cache_key}_daily.csv`
  - 美股日内：`data_cache/us_{SYM}_{timeframe}.csv`
  - 加密货币：`data_cache/crypto_{PAIR}_{daily|timeframe}.csv`

### 3. 双均线支持
**原因**：
- 买入/卖出可不同周期，提供更多策略空间
- 相同周期时退化为单均线策略
- 配合 `require_ma_order` 可避免"一日游"

### 4. 报告仅存内存
**原因**：
- UI 模式下避免大量临时文件
- 用户需保存时手动"另存为"
- 数据缓存保留，可重新生成

### 5. Web UI 与 CLI 共享逻辑
**原因**：
- `core.execute()` 是唯一入口
- `args.save=False` 时 UI 模式，`save=True` 时 CLI 模式
- 避免代码重复，保证一致性

---

## Pitfalls & Gotchas

### 1. 均线预热问题
**问题**：N 日均线前 N-1 个点为 NaN，导致信号错误
**解决**：`fetch_bars(..., warmup=max(2*longest, longest+5))`
**位置**：`core.py` 的 `run_one()` 函数

### 2. 次日开盘成交的边界
**问题**：最后一个 bar 买入时，次日无开盘价
**解决**：`engine.py` 中 `if exec_mode == "next_open" and i == len(df) - 1: continue`

### 3. 信号类型严格为 int
**问题**：`pd.Series(dtype=int)` 如果传入 float 会自动转换
**解决**：strategy 返回时显式 `.astype(int)`

### 4. 多标的对比时使用第一个标的的数据
**问题**：`optimize` 仅在第一个标的上运行
**位置**：`core.py` 第 161 行

### 5. Proxy 环境变量
**问题**：默认绕过系统代理
**解决**：设置 `BACKTEST_USE_PROXY=1` 如果需要走代理

### 6. macOS/Linux 启动
**问题**：`start_ui.bat` 是 Windows 专用
**解决**：见 README 的 macOS/Linux 启动说明

### 7. 加密货币白名单与市场类型
**问题**：`BNB` 等字母代码若不先被 `_is_crypto` 捕获，会被 `_is_us` 误判为美股。
**解决**：加密货币必须先走 `_crypto_route` / `_is_crypto`；当前仅支持 BTC/ETH/BNB 及对应 USDT 现货交易对。

### 8. Binance Spot vs Futures
**问题**：相邻项目 `D:\Others\indicator\data` 是 Binance USD-M Futures perpetual，路径为 `data/futures/um/...`，不符合本项目“现货”要求。
**解决**：本项目只能用 Binance Spot K 线入口和现货交易对；不要复用 futures CSV、`fapi` endpoint 或 `futures/um` raw archive。

### 9. 加密货币日线时区
**问题**：Binance 默认 K 线按 UTC+0 切分，和北京时间自然日不同。
**解决**：代码按 UTC open time 建索引；日线标签 `YYYY-MM-DD` 对应 UTC 当日 00:00 开盘，即北京时间当日 08:00 到次日 07:59:59.999。

---

## File Structure Reference

```
backtest/
├── src/
│   ├── app.py          # Flask UI，表单→参数→core.execute
│   ├── core.py         # 编排层，串联各模块
│   ├── data.py         # 数据获取，多源路由+缓存
│   ├── strategy.py     # 策略基类+均线策略
│   ├── engine.py       # 回测引擎，信号→权益曲线
│   ├── metrics.py      # 绩效计算
│   ├── optimize.py     # 参数寻优
│   └── report.py       # HTML 报告生成
├── data_cache/         # 数据缓存目录（.gitignore）
│   ├── sh000688_daily.csv
│   ├── us_SOXL_daily.csv
│   ├── us_SOXL_1h.csv
│   ├── crypto_BTCUSDT_daily.csv
│   ├── crypto_ETHUSDT_1h.csv
│   └── crypto_BNBUSDT_daily.csv
├── .venv/              # Windows 虚拟环境（.gitignore）
├── start_ui.bat        # Windows 一键启动
├── requirements.txt    # Python 依赖
├── README.md           # 用户文档
└── Agent.md            # 本文档
```

---

## Quick Reference for Common Edits

| 需求 | 修改位置 | 关键函数/行 |
|------|---------|------------|
| 添加新数据源 | src/data.py | `fetch_bars()` 添加路由 |
| 添加新策略 | src strategy.py | 新建 Strategy 子类 |
| 修改成交逻辑 | src/engine.py | `BacktestEngine.run()` |
| 添加新指标 | src/metrics.py | `compute_metrics()` |
| 修改报告布局 | src/report.py | `render_html()` |
| 添加 UI 参数 | src/app.py | PAGE HTML 模板 + `/run` 处理 |
| 修改缓存路径 | src/data.py | `CACHE_DIR` 常量 |
| 调整默认参数 | src/app.py | 表单默认值 |

---

## Testing Guidelines

### 单元测试文件命名
- `test_{module}.py` 在项目根目录
- 例：`test_strategy.py`, `test_engine.py`

### 关键测试点
1. **strategy.py**: 信号生成正确性，边界条件
2. **engine.py**: 权益计算，交易明细，成交时点
3. **metrics.py**: 各指标计算公式
4. **data.py**: 数据标准化，缓存读写

### 运行测试
```bash
.venv\Scripts\python -m pytest
.venv\Scripts\python -m pytest test_strategy.py -v
```

---

*此文档帮助 AI 快速理解项目结构。如有疑问，优先查看代码注释和类型注解。*
