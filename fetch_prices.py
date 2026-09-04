# -*- coding: utf-8 -*-
"""
Mars AI 控制台 - GitHub Actions 行情抓取脚本
由 .github/workflows/update-prices.yml 定时调用，只用 Python 标准库，
抓取 加密/美股/黄金/汇率 实时价格并写出 data.json 到仓库根目录。

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


def http_get_json(url, timeout=10, headers=None):
    req = urllib.request.Request(
        url,
        headers=headers or {"User-Agent": "Mozilla/5.0 MarsAI-Console"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_crypto():
    """优先 OKX（国内/Actions 均可访问），fallback 到 Binance / CoinGecko。"""
    syms = {
        "BTC-5M": "BTC-USDT", "ETH-1H": "ETH-USDT", "SOL-15M": "SOL-USDT",
        "DOGE-5M": "DOGE-USDT", "XRP-5M": "XRP-USDT",
        "MATIC-30M": "POL-USDT",  # MATIC 已迁移为 POL
    }
    # OKX 公开 API
    try:
        out = {}
        for k, inst in syms.items():
            try:
                d = http_get_json(f"https://www.okx.com/api/v5/market/ticker?instId={inst}")
                out[k] = float(d["data"][0]["last"])
            except Exception as e:
                print(f"[crypto][okx] {k}/{inst} failed: {e}")
        if out:
            print("[crypto] okx success:", list(out.keys()))
            return out, "okx"
    except Exception as e:
        print("[crypto] okx overall failed:", e)

    # Binance（Actions 可能因网络不可达）
    binance_syms = {
        "BTC-5M": "BTCUSDT", "ETH-1H": "ETHUSDT", "SOL-15M": "SOLUSDT",
        "DOGE-5M": "DOGEUSDT", "XRP-5M": "XRPUSDT", "MATIC-30M": "MATICUSDT",
    }
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbols=" + json.dumps(list(binance_syms.values())).replace(" ", "")
        data = http_get_json(url)
        m = {d["symbol"]: float(d["price"]) for d in data}
        out = {k: m[v] for k, v in binance_syms.items() if v in m}
        if "MATIC-30M" not in out:
            try:
                p = http_get_json("https://api.binance.com/api/v3/ticker/price?symbol=POLUSDT")
                out["MATIC-30M"] = float(p["price"])
            except Exception as e:
                print("[crypto][binance] POL fallback failed:", e)
        if out:
            print("[crypto] binance success:", list(out.keys()))
            return out, "binance"
    except Exception as e:
        print("[crypto] binance failed:", e)

    # CoinGecko 兜底
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,polygon,dogecoin,ripple&vs_currencies=usd"
        d = http_get_json(url)
        out = {
            "BTC-5M": d["bitcoin"]["usd"],
            "ETH-1H": d["ethereum"]["usd"],
            "SOL-15M": d["solana"]["usd"],
            "MATIC-30M": d["polygon"]["usd"],
            "DOGE-5M": d["dogecoin"]["usd"],
            "XRP-5M": d["ripple"]["usd"],
        }
        print("[crypto] coingecko success:", list(out.keys()))
        return out, "coingecko"
    except Exception as e:
        print("[crypto] coingecko failed:", e)

    return {}, "failed"


def fetch_us_stocks():
    syms = {
        "英伟达[NVIDIA]": "NVDA", "特斯拉[TSLA]": "TSLA",
        "AAPL": "AAPL", "MSFT": "MSFT", "GOOGL": "GOOGL", "AMZN": "AMZN"
    }
    # 优先 Yahoo Finance v8（更标准）
    out = {}
    try:
        for k, code in syms.items():
            try:
                url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}"
                d = http_get_json(url, headers={"User-Agent": "Mozilla/5.0"})
                price = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
                if price:
                    out[k] = float(price)
            except Exception as e:
                print(f"[stocks][yahoo] {code} failed: {e}")
        if out:
            print("[stocks] yahoo success:", list(out.keys()))
            return out, "yahoo-finance"
    except Exception as e:
        print("[stocks] yahoo overall failed:", e)

    # fallback 腾讯 gtimg
    try:
        out = {}
        for k, code in syms.items():
            try:
                url = f"https://qt.gtimg.cn/q=us{code}"
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    raw = r.read().decode("gbk")
                parts = raw.split("=")[1].strip('"').split("~")
                if len(parts) > 3 and parts[3]:
                    out[k] = float(parts[3])
            except Exception as e:
                print(f"[stocks][tencent] us{code} failed: {e}")
        if out:
            print("[stocks] tencent success:", list(out.keys()))
            return out, "tencent-gtimg"
    except Exception as e:
        print("[stocks] tencent failed:", e)

    return {}, "failed"


def fetch_gold():
    # 优先 Yahoo Finance 黄金期货 GC=F
    try:
        d = http_get_json("https://query1.finance.yahoo.com/v8/finance/chart/GC=F")
        price = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
        if price:
            return {"黄金[XAU]": float(price)}, "yahoo-gc=f"
    except Exception as e:
        print("[gold] yahoo GC=F failed:", e)

    # fallback gold-api（注意其返回的 USD 数值有时等价于人民币/克，但经校验与 GC=F 接近）
    try:
        d = http_get_json("https://api.gold-api.com/price/XAU")
        return {"黄金[XAU]": float(d["price"])}, "gold-api"
    except Exception as e:
        print("[gold] gold-api failed:", e)

    return {}, "failed"


def fetch_fx():
    try:
        d = http_get_json("https://open.er-api.com/v6/latest/USD")
        return {"美元": float(d["rates"]["CNY"])}, "exchangerate"
    except Exception as e:
        print("[fx] exchangerate failed:", e)
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
