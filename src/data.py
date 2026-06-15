"""数据层：获取 A 股指数 / 全球指数 / 美股个股日线数据。

支持两类标的，按代码自动判别（见 _is_us）：
  - A 股指数：纯数字代码（如 000688）或带 sh/sz 前缀。
  - 全球/港股指数：常用别名（如 日经225/N225、恒生指数/HSI、恒生科技/HSTECH）。
  - 美股个股/ETF：含字母的代码（如 SOXL、TQQQ、AAPL）。

数据源策略（按此环境实测可靠性排序）：
  A 股指数：
    1) 新浪财经  ak.stock_zh_index_daily        —— 主源，返回全历史 OHLCV
    2) 腾讯财经  ak.stock_zh_index_daily_tx     —— 备用源
  美股个股：
    1) 新浪财经  ak.stock_us_daily              —— 返回全历史 OHLCV，列与 A 股源一致
  全球指数：
    1) 新浪财经  ak.index_global_hist_sina      —— 主源
    2) 东方财富  ak.index_global_hist_em        —— 备用源
  港股指数：
    1) 新浪财经  ak.stock_hk_index_daily_sina   —— 主源
    2) 东方财富  ak.stock_hk_index_daily_em     —— 备用源

缓存与覆盖逻辑（每个指数只保存一个 CSV，内容始终是"最早～最新"的最全数据）：
  - 日线数据源通常【没有日期参数】，单次请求即返回该标的可得的全部历史，
    无法"只请求缺失的一段"。因此一次性拉全量既是唯一可行方式，也最省事。
  - 请求时先看本地缓存：若缓存已覆盖请求区间（起点足够早、且数据足够新）→ 直接用本地，不联网。
  - 若缓存缺更早的数据，或不够新 → 联网拉全量，与旧缓存【按日期取并集合并】后回写，
    从而把缓存扩展为更宽的区间（如上次存到 2021 起、这次需要 2020，则合并后保留 2020～最新）。
  - 同一天内已联网更新过则不再重复联网（按缓存文件修改日期判断），避免无谓请求。
  - refresh=True 可强制重新拉取并覆盖。

注意：东方财富 index_zh_a_hist 在部分网络环境会主动断开连接，故不作为默认源。
"""

from __future__ import annotations

import os
import time
import datetime as dt

import pandas as pd

# 许多机器（尤其 Windows）在系统层配置了无法连通数据站点的代理，requests 会自动读取并使用它，
# 导致 ProxyError。数据站点均为公网，这里默认绕过系统代理；如确需代理，设环境变量 BACKTEST_USE_PROXY=1。
if os.environ.get("BACKTEST_USE_PROXY") != "1":
    os.environ.setdefault("no_proxy", "*")
    os.environ.setdefault("NO_PROXY", "*")

# 本文件已移入 src/ 子目录；数据缓存仍放在项目根目录的 data_cache/（属数据而非代码）
CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache"
)

# 标准列：date(index, datetime) / open / high / low / close / volume
_STD_COLS = ["open", "high", "low", "close", "volume"]

_GLOBAL_SINA_NAME = {
    "日经225": "日经225指数",
}

_SPECIAL_DAILY_ROUTES = {
    # 全球指数
    "日经225": {"kind": "global", "fetch_id": "日经225", "cache_key": "global_N225", "label": "日经225"},
    "n225": {"kind": "global", "fetch_id": "日经225", "cache_key": "global_N225", "label": "日经225"},
    "nikkei225": {"kind": "global", "fetch_id": "日经225", "cache_key": "global_N225", "label": "日经225"},
    "nikkei": {"kind": "global", "fetch_id": "日经225", "cache_key": "global_N225", "label": "日经225"},
    # 港股指数
    "恒生指数": {"kind": "hk", "fetch_id": "HSI", "cache_key": "hk_HSI", "label": "恒生指数"},
    "hsi": {"kind": "hk", "fetch_id": "HSI", "cache_key": "hk_HSI", "label": "恒生指数"},
    "恒指": {"kind": "hk", "fetch_id": "HSI", "cache_key": "hk_HSI", "label": "恒生指数"},
    "恒生科技": {"kind": "hk", "fetch_id": "HSTECH", "cache_key": "hk_HSTECH", "label": "恒生科技"},
    "恒生科技指数": {"kind": "hk", "fetch_id": "HSTECH", "cache_key": "hk_HSTECH", "label": "恒生科技"},
    "hstech": {"kind": "hk", "fetch_id": "HSTECH", "cache_key": "hk_HSTECH", "label": "恒生科技"},
}


def _alias_key(code: str) -> str:
    return str(code).strip().lower().replace(" ", "").replace("_", "").replace("-", "")


def _special_daily_route(code: str) -> dict | None:
    return _SPECIAL_DAILY_ROUTES.get(_alias_key(code))


def _is_us(code: str) -> bool:
    """判断标的是否为美股个股/ETF。

    规则：去空格后，以 sh/sz 前缀开头、或全为数字 → A 股指数；否则（含字母，如 SOXL/AAPL）→ 美股。
    """
    code = str(code).strip()
    if not code:
        return False
    if _special_daily_route(code):
        return False
    low = code.lower()
    if low.startswith(("sh", "sz", "bj")):
        return False
    return not code.isdigit()


def to_sina_symbol(code: str) -> str:
    """把标的代码转成数据源所需的规范形式。

    - 美股：统一转大写原样返回（如 soxl -> SOXL），不加市场前缀。
    - A 股指数：北证系列指数（899xxx）用 bj；深证系列指数（399xxx / 395xxx）用 sz，
      其余（上证、沪深300、中证、科创等）用 sh。
      若已带前缀（sh/sz/bj 开头）则原样返回。
    """
    code = str(code).strip()
    route = _special_daily_route(code)
    if route:
        return route["fetch_id"]
    if _is_us(code):
        return code.upper()
    code = code.lower()
    if code.startswith(("sh", "sz", "bj")):
        return code
    if code.startswith("899"):
        return "bj" + code
    if code.startswith(("399", "395")):
        return "sz" + code
    return "sh" + code


def daily_symbol_label(code: str) -> str | None:
    """返回特殊日线标的的显示名称；非特殊标的返回 None。"""
    route = _special_daily_route(code)
    return route["label"] if route else None


def _pick(df: pd.DataFrame, candidates: list[str], required: bool = True) -> str | None:
    """按候选列名挑出数据源实际返回的列，兼容中英文和大小写。"""
    lower = {str(c).strip().lower(): c for c in df.columns}
    for c in candidates:
        if c in df.columns:
            return c
        key = str(c).strip().lower()
        if key in lower:
            return lower[key]
    if required:
        raise KeyError(f"列未找到，候选={candidates}，实际={list(df.columns)}")
    return None


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """把中英文列名的指数数据统一为标准 OHLCV；缺少 OHLC 时用 close 兜底。"""
    df = df.copy()
    date_col = _pick(df, ["date", "日期", "datetime"])
    close_col = _pick(df, ["close", "收盘", "收盘价", "最新价", "latest"])
    open_col = _pick(df, ["open", "开盘", "开盘价", "今开"], required=False)
    high_col = _pick(df, ["high", "最高", "最高价"], required=False)
    low_col = _pick(df, ["low", "最低", "最低价"], required=False)
    volume_col = _pick(df, ["volume", "成交量", "amount", "成交额"], required=False)

    out = pd.DataFrame()
    out["date"] = pd.to_datetime(df[date_col])
    out["close"] = pd.to_numeric(df[close_col], errors="coerce")
    out["open"] = pd.to_numeric(df[open_col], errors="coerce") if open_col else out["close"]
    out["high"] = pd.to_numeric(df[high_col], errors="coerce") if high_col else out["close"]
    out["low"] = pd.to_numeric(df[low_col], errors="coerce") if low_col else out["close"]
    out["volume"] = pd.to_numeric(df[volume_col], errors="coerce") if volume_col else pd.NA
    out = out[["date"] + _STD_COLS].dropna(subset=["close"])
    out = out.sort_values("date").drop_duplicates(subset="date").set_index("date")
    return out


def _daily_route(symbol: str) -> dict:
    route = _special_daily_route(symbol)
    if route:
        return route
    sina_symbol = to_sina_symbol(symbol)
    if _is_us(symbol):
        return {"kind": "us", "fetch_id": sina_symbol, "cache_key": f"us_{sina_symbol}", "label": sina_symbol}
    return {"kind": "a_index", "fetch_id": sina_symbol, "cache_key": sina_symbol, "label": sina_symbol}


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    """统一不同数据源的列名/类型，返回以 datetime 为索引、含标准 OHLCV 列、按日期升序的 DataFrame。"""
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    if "date" not in df.columns:
        raise ValueError(f"数据源返回缺少 date 列：{list(df.columns)}")
    df["date"] = pd.to_datetime(df["date"])
    # 成交量列在腾讯源可能叫 amount（成交额）；统一兜底
    if "volume" not in df.columns:
        df["volume"] = df["amount"] if "amount" in df.columns else pd.NA
    for col in _STD_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df[["date"] + _STD_COLS].dropna(subset=["close"])
    df = df.sort_values("date").drop_duplicates(subset="date").set_index("date")
    return df


def _fetch_raw(sina_symbol: str, retries: int = 3) -> pd.DataFrame:
    """从主源/备用源拉取全历史，带重试与指数退避。返回标准化 DataFrame。

    美股走 ak.stock_us_daily；A 股指数走新浪 / 腾讯指数源。两类源均无日期参数、单次返回全历史。
    """
    import akshare as ak

    if _is_us(sina_symbol):
        sources = [
            ("sina_us", lambda: ak.stock_us_daily(symbol=sina_symbol)),
        ]
    else:
        sources = [
            ("sina", lambda: ak.stock_zh_index_daily(symbol=sina_symbol)),
            ("tencent", lambda: ak.stock_zh_index_daily_tx(symbol=sina_symbol)),
        ]
    last_err: Exception | None = None
    for name, call in sources:
        for attempt in range(1, retries + 1):
            try:
                df = call()
                if df is None or len(df) == 0:
                    raise ValueError("返回空数据")
                return _normalize(df)
            except Exception as e:  # noqa: BLE001  —— 网络/解析异常统一重试
                last_err = e
                wait = 1.5 * attempt
                print(f"  [{name}] 第 {attempt}/{retries} 次获取失败：{type(e).__name__}: {str(e)[:80]}；{wait:.1f}s 后重试")
                time.sleep(wait)
        print(f"  [{name}] 源不可用，尝试下一个源 ...")
    raise ConnectionError(f"所有数据源均获取失败：{type(last_err).__name__}: {last_err}")


def _fetch_global_raw(fetch_id: str, retries: int = 3) -> pd.DataFrame:
    """获取海外指数日线。新浪全球指数为主，东方财富全球指数兜底。"""
    import akshare as ak

    sources = []
    sina_name = _GLOBAL_SINA_NAME.get(fetch_id)
    if sina_name:
        sources.append(("sina_global", lambda: ak.index_global_hist_sina(symbol=sina_name)))
    sources.append(("em_global", lambda: ak.index_global_hist_em(symbol=fetch_id)))

    last_err: Exception | None = None
    for name, call in sources:
        for attempt in range(1, retries + 1):
            try:
                df = call()
                if df is None or len(df) == 0:
                    raise ValueError("返回空数据")
                return _normalize_ohlcv(df)
            except Exception as e:  # noqa: BLE001
                last_err = e
                wait = 1.5 * attempt
                print(f"  [{name}] 第 {attempt}/{retries} 次获取失败：{type(e).__name__}: {str(e)[:80]}；{wait:.1f}s 后重试")
                time.sleep(wait)
        print(f"  [{name}] 源不可用，尝试下一个源 ...")
    raise ConnectionError(f"所有全球指数数据源均获取失败：{type(last_err).__name__}: {last_err}")


def _fetch_hk_raw(fetch_id: str, retries: int = 3) -> pd.DataFrame:
    """获取港股指数日线。新浪港股指数为主，东方财富港股指数兜底。"""
    import akshare as ak

    sources = [
        ("sina_hk", lambda: ak.stock_hk_index_daily_sina(symbol=fetch_id)),
        ("em_hk", lambda: ak.stock_hk_index_daily_em(symbol=fetch_id)),
    ]
    last_err: Exception | None = None
    for name, call in sources:
        for attempt in range(1, retries + 1):
            try:
                df = call()
                if df is None or len(df) == 0:
                    raise ValueError("返回空数据")
                return _normalize_ohlcv(df)
            except Exception as e:  # noqa: BLE001
                last_err = e
                wait = 1.5 * attempt
                print(f"  [{name}] 第 {attempt}/{retries} 次获取失败：{type(e).__name__}: {str(e)[:80]}；{wait:.1f}s 后重试")
                time.sleep(wait)
        print(f"  [{name}] 源不可用，尝试下一个源 ...")
    raise ConnectionError(f"所有港股指数数据源均获取失败：{type(last_err).__name__}: {last_err}")


def _fetch_daily_raw(route: dict) -> pd.DataFrame:
    kind = route["kind"]
    if kind in ("a_index", "us"):
        return _fetch_raw(route["fetch_id"])
    if kind == "global":
        return _fetch_global_raw(route["fetch_id"])
    if kind == "hk":
        return _fetch_hk_raw(route["fetch_id"])
    raise ValueError(f"未知日线数据路由：{route}")


def _read_cache(path: str) -> pd.DataFrame | None:
    """读取本地缓存 CSV，返回标准化（datetime 索引、标准列、升序）的 DataFrame；无缓存或损坏返回 None。"""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path, parse_dates=["date"], index_col="date")
        for col in _STD_COLS:
            if col not in df.columns:
                df[col] = pd.NA
        df = df[_STD_COLS].sort_index()
        return df if not df.empty else None
    except Exception as e:  # noqa: BLE001
        print(f"  缓存读取失败（将重新联网获取）：{type(e).__name__}: {e}")
        return None


def fetch_index_daily(
    symbol: str,
    start: str | None = None,
    end: str | None = None,
    warmup: int = 0,
    refresh: bool = False,
) -> pd.DataFrame:
    """获取指定指数的日线数据。

    参数
    ----
    symbol  : 标的代码。A 股指数如 '000688'；全球/港股指数如 'N225'/'HSTECH'/'HSI'；美股如 'SOXL'。
    start   : 回测起始日期 'YYYY-MM-DD'，None 表示不裁剪左端。
    end     : 回测结束日期 'YYYY-MM-DD'，None 表示取到最新交易日。
    warmup  : 均线预热——在 start 之前额外保留的交易日数，使 start 当日均线已成形。
    refresh : True 则忽略缓存强制重新拉取。

    返回
    ----
    DataFrame，datetime 索引，列 = [open, high, low, close, volume]，按日期升序。
    含 warmup 段（位于 start 之前），绩效统计时应只从 start 起算。
    """
    route = _daily_route(symbol)
    fetch_id = route["fetch_id"]
    os.makedirs(CACHE_DIR, exist_ok=True)
    # 非 A 股缓存加来源前缀，避免与 A 股代码空间冲突（如 sh000688_daily.csv vs us_SOXL_daily.csv）
    cache_key = route["cache_key"]
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}_daily.csv")

    cached = _read_cache(cache_path)  # 没有缓存时返回 None
    today = dt.date.today()
    start_ts = pd.to_datetime(start) if start else None
    end_ts = pd.to_datetime(end) if end else pd.Timestamp(today)

    # ---- 第 1 步：判断本地缓存是否足够，决定是否联网 ----
    need_fetch, reason = False, ""
    if refresh:
        need_fetch, reason = True, "强制刷新"
    elif cached is None:
        need_fetch, reason = True, "本地无缓存"
    else:
        cache_start, cache_end = cached.index.min(), cached.index.max()
        # 同一天内已联网更新过的缓存视为足够新，不再重复联网
        fetched_today = dt.date.fromtimestamp(os.path.getmtime(cache_path)) == today
        if start_ts is not None and start_ts < cache_start and not fetched_today:
            need_fetch = True
            reason = f"请求起始 {start_ts.date()} 早于本地最早 {cache_start.date()}，补取更早数据"
        elif end_ts > cache_end and not fetched_today:
            need_fetch = True
            reason = f"请求截止 {end_ts.date()} 晚于本地最新 {cache_end.date()}，更新最新数据"

    # ---- 第 2 步：按需联网拉全量，并与旧缓存合并取并集（保留最早～最新） ----
    if need_fetch:
        print(f"获取数据：{symbol} -> {fetch_id}（{reason}；日线数据源）")
        fresh = _fetch_daily_raw(route)
        if cached is not None:
            # 合并去重：同一日期以最新拉取的为准
            df = pd.concat([cached, fresh])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        else:
            df = fresh
        df.to_csv(cache_path, encoding="utf-8")
        print(f"  已更新缓存：{len(df)} 行（{df.index.min().date()} ~ {df.index.max().date()}）-> {cache_path}")
    else:
        df = cached
        print(f"使用本地缓存（已覆盖请求区间）：{len(df)} 行，"
              f"{df.index.min().date()} ~ {df.index.max().date()}")

    full_start, full_end = df.index.min(), df.index.max()
    if start_ts is not None and start_ts < full_start:
        print(f"  注意：请求起始 {start_ts.date()} 早于该指数最早可得数据 {full_start.date()}，"
              f"将从最早可得数据开始（已是全部可用历史）。")

    # 按 end 裁剪右端
    if end is not None:
        df = df[df.index <= pd.to_datetime(end)]

    # 按 start 裁剪左端，但向前保留 warmup 个交易日用于均线预热
    if start is not None:
        start_ts = pd.to_datetime(start)
        if warmup > 0:
            pos = df.index.searchsorted(start_ts)  # start 在索引中的位置
            keep_from = max(0, pos - warmup)
            df = df.iloc[keep_from:]
        else:
            df = df[df.index >= start_ts]

    if df.empty:
        raise ValueError(
            f"裁剪后无数据。请求区间 [{start} ~ {end}]，但 {symbol} 可用数据为 "
            f"[{full_start.date()} ~ {full_end.date()}]。可能起始日早于该指数成立日。"
        )
    return df


# ============================ 日内（intraday）数据：Twelve Data ============================
# 新浪/腾讯只给日线；美股日内（4h 等）改用 Twelve Data（原生支持 4h/2h/1h/30m，免费档 ~7 年历史）。
# 需要免费 API key：环境变量 TWELVEDATA_API_KEY，或写在 src/.env 里 TWELVEDATA_API_KEY=xxx。

# 本工具的时间框架 -> Twelve Data 的 interval（均为其原生支持，无需本地合成）
TIMEFRAME_TO_TD = {
    "30m": "30min", "1h": "1h", "2h": "2h", "4h": "4h",
}
# 每个交易日的 bar 数（仅正常盘 9:30–16:00）；用于年化因子兜底，实际以数据推算为准
TF_BARS_PER_DAY = {"30m": 13, "1h": 7, "2h": 4, "4h": 2}


def _load_env_key(name: str) -> str | None:
    """读取 API key：优先环境变量；否则从 src/.env 读取 `name=value`（允许空格/引号）。"""
    val = os.environ.get(name)
    if val:
        return val.strip()
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        import re
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                m = re.match(rf"\s*{re.escape(name)}\s*=\s*['\"]?([^'\"\s]+)", line)
                if m:
                    return m.group(1)
    return None


def _fetch_twelvedata(symbol: str, interval: str, outputsize: int = 5000,
                      retries: int = 3) -> pd.DataFrame:
    """从 Twelve Data 拉取指定 interval 的全部可得 bar（最多 outputsize 根），返回标准化 DataFrame。"""
    import json
    import urllib.request
    import urllib.parse

    key = _load_env_key("TWELVEDATA_API_KEY") or _load_env_key("TWELVE_DATA_API_KEY")
    if not key:
        raise RuntimeError(
            "未找到 TWELVEDATA_API_KEY。请在 src/.env 写入 TWELVEDATA_API_KEY=你的key，"
            "或设环境变量。免费 key：https://twelvedata.com/account/api-keys")

    params = urllib.parse.urlencode({
        "symbol": symbol, "interval": interval, "outputsize": outputsize,
        "order": "ASC", "timezone": "America/New_York", "apikey": key,
    })
    url = f"https://api.twelvedata.com/time_series?{params}"

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
            j = json.loads(raw)
            if isinstance(j, dict) and j.get("status") == "error":
                # 包括无效 key、限速、未知标的等——直接报错，不重试无意义的（限速除外）
                msg = j.get("message", str(j))
                if "limit" in msg.lower() or "run out" in msg.lower():
                    raise ConnectionError(f"Twelve Data 限速：{msg}")
                raise ValueError(f"Twelve Data 返回错误：{msg}")
            values = j.get("values") if isinstance(j, dict) else None
            if not values:
                raise ValueError(f"Twelve Data 返回空数据：{str(j)[:120]}")
            df = pd.DataFrame(values).rename(columns={"datetime": "date"})
            return _normalize(df)
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 1.5 * attempt
            print(f"  [twelvedata] 第 {attempt}/{retries} 次获取失败：{type(e).__name__}: {str(e)[:90]}；{wait:.1f}s 后重试")
            time.sleep(wait)
    raise ConnectionError(f"Twelve Data 获取失败：{type(last_err).__name__}: {last_err}")


def fetch_intraday(
    symbol: str,
    timeframe: str,
    start: str | None = None,
    end: str | None = None,
    warmup: int = 0,
    refresh: bool = False,
) -> pd.DataFrame:
    """获取美股日内数据（4h/2h/1h/30m）。结构与 fetch_index_daily 对齐：含 warmup、按 start/end 裁剪。

    缓存：data_cache/us_{SYM}_{timeframe}.csv，与日线相同的「按日期并集合并」逻辑——
    Twelve Data 单次最多返回 outputsize 根，定期运行可让本地历史随时间增长、突破单次窗口。
    """
    if not _is_us(symbol):
        raise ValueError(f"日内回测目前仅支持美股标的（如 SOXL）；非美股标的 {symbol} 暂不支持日内。")
    if timeframe not in TIMEFRAME_TO_TD:
        raise ValueError(f"不支持的时间框架 {timeframe}；可选：{list(TIMEFRAME_TO_TD)}")

    sym = symbol.strip().upper()
    interval = TIMEFRAME_TO_TD[timeframe]
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"us_{sym}_{timeframe}.csv")

    cached = _read_cache(cache_path)
    today = dt.date.today()
    need_fetch, reason = False, ""
    if refresh:
        need_fetch, reason = True, "强制刷新"
    elif cached is None:
        need_fetch, reason = True, "本地无缓存"
    else:
        fetched_today = dt.date.fromtimestamp(os.path.getmtime(cache_path)) == today
        end_ts = pd.to_datetime(end) if end else pd.Timestamp(today)
        if not fetched_today and end_ts.normalize() >= cached.index.max().normalize():
            need_fetch, reason = True, "更新最新 bar"

    if need_fetch:
        print(f"获取日内数据：{sym} {timeframe}（interval={interval}；{reason}）")
        fresh = _fetch_twelvedata(sym, interval)
        if cached is not None:
            df = pd.concat([cached, fresh])
            df = df[~df.index.duplicated(keep="last")].sort_index()
        else:
            df = fresh
        df.to_csv(cache_path, encoding="utf-8")
        print(f"  已更新缓存：{len(df)} 根（{df.index.min()} ~ {df.index.max()}）-> {cache_path}")
    else:
        df = cached
        print(f"使用本地缓存：{len(df)} 根，{df.index.min()} ~ {df.index.max()}")

    full_start, full_end = df.index.min(), df.index.max()
    if end is not None:
        df = df[df.index <= pd.to_datetime(end)]
    if start is not None:
        start_ts = pd.to_datetime(start)
        if warmup > 0:
            pos = df.index.searchsorted(start_ts)
            df = df.iloc[max(0, pos - warmup):]
        else:
            df = df[df.index >= start_ts]

    if df.empty:
        raise ValueError(
            f"裁剪后无数据。请求区间 [{start} ~ {end}]，但 {sym} {timeframe} 可用数据为 "
            f"[{full_start} ~ {full_end}]。")
    return df


def fetch_bars(
    symbol: str,
    timeframe: str = "1d",
    start: str | None = None,
    end: str | None = None,
    warmup: int = 0,
    refresh: bool = False,
) -> pd.DataFrame:
    """统一取数入口：日线走 fetch_index_daily（A股/全球/港股/美股皆可），日内走 fetch_intraday（仅美股）。"""
    if timeframe in (None, "1d", "日线", "day", "daily"):
        return fetch_index_daily(symbol, start=start, end=end, warmup=warmup, refresh=refresh)
    return fetch_intraday(symbol, timeframe, start=start, end=end, warmup=warmup, refresh=refresh)


if __name__ == "__main__":
    # 简易自测：A 股指数 + 美股各一
    d = fetch_index_daily("000688", start="2021-01-01", warmup=40)
    print(d.head())
    print("...")
    print(d.tail())
    print("行数:", len(d))

    u = fetch_index_daily("SOXL", start="2021-01-01", warmup=40)
    print(u.tail())
    print("SOXL 行数:", len(u))
