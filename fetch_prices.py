# -*- coding: utf-8 -*-
"""
Mars AI 控制台 - GitHub Actions 行情抓取脚本
由 .github/workflows/update-prices.yml 定时调用，只用 Python 标准库，
抓取 加密/美股/黄金/汇率 实时价格并写出 data.json 到仓库根目录。

网络稳健性设计（v2）：
  1. 所有 HTTP 请求都带「指数退避重试」，单点网络抖动可自动恢复；
  2. 加密 OKX 优先 -> CoinGecko 兜底；
  3. 美股/黄金 改为「腾讯 gtimg 优先」，Yahoo 降级为兜底（Yahoo 在 Actions
     runner 上经常被限流/超时，是此前行情卡顿的主因）；
  4. 美股用腾讯「批量」接口，一次请求拿全部，减少外部依赖面。

输出结构与 mars-ai-backend/app.py 的 /api/prices 完全一致：
{ "backend": "github-actions", "updatedAt": ..., "sources": {...}, "prices": {...} }
前端部署态会直接读取同域的 ./data.json。
"""
import json
import os
import time
import urllib.request
import urllib.error

# 与 HTML marketBasePrices 默认兜底值保持一致
DEFAULTS = {
    "BTC-5M": 81150.00, "ETH-1H": 2530.00, "SOL-15M": 104.30, "MATIC-30M": 0.42,
    "DOGE-5M": 0.088, "XRP-5M": 1.46,
    "英伟达[NVIDIA]": 228.45, "特斯拉[TSLA]": 376.37, "长鑫科技": 58.30, "宇树科技": 89.60,
    "AAPL": 328.21, "MSFT": 510.12, "GOOGL": 342.48, "AMZN": 258.90,
    "黄金[XAU]": 4525.40, "美元": 6.74, "人民币": 1.00,
    "超级碗2024": 0.72, "NBA决赛": 1.45,
}


def http_get_json(url, timeout=12, retries=3, backoff=1.5, headers=None):
    """带指数退避的 JSON GET。网络超时/5xx/解析失败都会重试。"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers=headers or {"User-Agent": "Mozilla/5.0 MarsAI-Console"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:  # noqa: BLE001 - 任何异常都重试
            last = e
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise last


def http_get_text(url, timeout=12, retries=3, backoff=1.5, encoding="utf-8", headers=None):
    """带指数退避的纯文本 GET（腾讯 gtimg 用 GBK，新浪用 GBK/UTF-8）。"""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers=headers or {"User-Agent": "Mozilla/5.0 MarsAI-Console"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode(encoding, "ignore")
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise last


def fetch_crypto():
    """OKX 优先（逐个，带重试）；不足时 CoinGecko 一次全拿兜底。"""
    syms = {
        "BTC-5M": "BTC-USDT", "ETH-1H": "ETH-USDT", "SOL-15M": "SOL-USDT",
        "DOGE-5M": "DOGE-USDT", "XRP-5M": "XRP-USDT",
        "MATIC-30M": "POL-USDT",  # MATIC 已迁移为 POL
    }
    out = {}
    try:
        for k, inst in syms.items():
            try:
                d = http_get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst}")
                out[k] = float(d["data"][0]["last"])
            except Exception as e:
                print(f"[crypto][okx] {k}/{inst} failed: {e}")
        if len(out) >= 4:
            print("[crypto] okx success:", list(out.keys()))
            return out, "okx"
    except Exception as e:
        print("[crypto] okx overall failed:", e)

    # CoinGecko 兜底：一次请求拿全部 6 个，最稳
    try:
        url = ("https://api.coingecko.com/api/v3/simple/price"
               "?ids=bitcoin,ethereum,solana,polygon,dogecoin,ripple&vs_currencies=usd")
        d = http_get_json(url)
        out.update({
            "BTC-5M": d["bitcoin"]["usd"],
            "ETH-1H": d["ethereum"]["usd"],
            "SOL-15M": d["solana"]["usd"],
            "MATIC-30M": d["polygon"]["usd"],
            "DOGE-5M": d["dogecoin"]["usd"],
            "XRP-5M": d["ripple"]["usd"],
        })
        print("[crypto] coingecko success:", list(out.keys()))
        return out, "coingecko"
    except Exception as e:
        print("[crypto] coingecko failed:", e)

    return out, "okx-partial" if out else "failed"


def fetch_us_stocks():
    """腾讯 gtimg 批量优先（1 个请求拿全部），Yahoo / 新浪 依次兜底。"""
    syms = {
        "英伟达[NVIDIA]": "NVDA", "特斯拉[TSLA]": "TSLA",
        "AAPL": "AAPL", "MSFT": "MSFT", "GOOGL": "GOOGL", "AMZN": "AMZN"
    }
    # 腾讯 gtimg 批量
    out = {}
    try:
        codes = ",".join("us" + c for c in syms.values())
        raw = http_get_text(
            f"https://qt.gtimg.cn/q={codes}",
            encoding="gbk",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        )
        for line in raw.split(";"):
            if "v_us" not in line or "=" not in line:
                continue
            code = "US" + line.split("v_us")[-1].split("=")[0].upper()
            body = line.split("=")[1].strip('"')
            parts = body.split("~")
            if len(parts) > 3 and parts[3]:
                for k, c in syms.items():
                    if c.upper() in code:
                        out[k] = float(parts[3])
                        break
        if out:
            print("[stocks] tencent success:", list(out.keys()))
            return out, "tencent-gtimg"
    except Exception as e:
        print("[stocks] tencent failed:", e)

    # Yahoo 兜底
    out2 = {}
    for k, c in syms.items():
        try:
            d = http_get_json(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{c}",
                retries=2,
            )
            p = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
            if p:
                out2[k] = float(p)
        except Exception as e:
            print(f"[stocks][yahoo] {c} failed: {e}")
    if out2:
        print("[stocks] yahoo success:", list(out2.keys()))
        return out2, "yahoo-finance"

    # 新浪兜底
    out3 = {}
    try:
        codes = ",".join("gb_" + c.lower() for c in syms.values())
        raw = http_get_text(
            f"https://hq.sinajs.cn/list={codes}",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"},
        )
        for line in raw.split("\n"):
            if "gb_" not in line or '"' not in line:
                continue
            c = line.split("gb_")[-1].split("=")[0].upper()
            body = line.split('"')[1]
            p0 = body.split(",")[0]
            if p0 and p0.strip():
                for k, cc in syms.items():
                    if cc.upper() == c:
                        out3[k] = float(p0)
                        break
        if out3:
            print("[stocks] sina success:", list(out3.keys()))
            return out3, "sina"
    except Exception as e:
        print("[stocks] sina failed:", e)

    return out, "tencent-partial" if out else "failed"


def fetch_gold():
    """Yahoo GC=F -> 腾讯 hf_GC（逗号分隔）-> gold-api 兜底。"""
    try:
        d = http_get_json("https://query1.finance.yahoo.com/v8/finance/chart/GC=F", retries=2)
        p = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
        if p:
            return {"黄金[XAU]": float(p)}, "yahoo-gc=f"
    except Exception as e:
        print("[gold] yahoo GC=F failed:", e)

    # 腾讯伦敦金 hf_GC：逗号分隔 -> 最新价,涨跌,买,卖,高,低,...
    try:
        raw = http_get_text(
            "https://qt.gtimg.cn/q=hf_GC",
            encoding="gbk",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"},
        )
        body = raw.split("=")[1].strip('"')
        parts = body.split(",")
        if parts and parts[0]:
            print("[gold] tencent hf_GC success:", parts[0])
            return {"黄金[XAU]": float(parts[0])}, "tencent-hf_GC"
    except Exception as e:
        print("[gold] tencent hf_GC failed:", e)

    try:
        d = http_get_json("https://api.gold-api.com/price/XAU")
        return {"黄金[XAU]": float(d["price"])}, "gold-api"
    except Exception as e:
        print("[gold] gold-api failed:", e)

    return {}, "failed"


def fetch_fx():
    """exchangerate 主源 -> exchangerate.host 兜底。"""
    try:
        d = http_get_json("https://open.er-api.com/v6/latest/USD")
        return {"美元": float(d["rates"]["CNY"])}, "exchangerate"
    except Exception as e:
        print("[fx] exchangerate failed:", e)
    try:
        d = http_get_json("https://api.exchangerate.host/latest?base=USD")
        return {"美元": float(d["rates"]["CNY"])}, "exchangerate-host"
    except Exception as e:
        print("[fx] exchangerate-host failed:", e)
    return {}, "failed"


def refresh():
    prices = dict(DEFAULTS)
    sources = {}
    for fn in [fetch_crypto, fetch_us_stocks, fetch_gold, fetch_fx]:
        try:
            res, src = fn()
            for k, v in res.items():
                prices[k] = v
                sources[k] = src
        except Exception as e:
            print(f"[{fn.__name__}] unexpected error: {e}")
    payload = {
        "backend": "github-actions",
        "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "sources": sources,
        "prices": prices,
    }
    out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print("[Mars AI] updatedAt:", payload["updatedAt"])
    print("[Mars AI] sources:", json.dumps(sources, ensure_ascii=False))
    print("[Mars AI] wrote", out_path)


if __name__ == "__main__":
    refresh()
