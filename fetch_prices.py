# -*- coding: utf-8 -*-
"""
Mars AI 控制台 - GitHub Actions 行情抓取脚本（无外部服务方案）
由 .github/workflows/update-prices.yml 定时调用，仅用 Python 标准库，
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
    "BTC-5M": 77521.30, "ETH-1H": 2417.00, "SOL-15M": 145.67, "MATIC-30M": 0.89,
    "DOGE-5M": 0.128, "XRP-5M": 0.68,
    "英伟达[NVIDIA]": 132.45, "特斯拉[TSLA]": 245.80, "长鑫科技": 58.30, "宇树科技": 89.60,
    "AAPL": 222.50, "MSFT": 455.30, "GOOGL": 176.80, "AMZN": 202.40,
    "黄金[XAU]": 2345.60, "美元": 7.18, "人民币": 1.00,
    "超级碗2024": 0.72, "NBA决赛": 1.45,
}


def http_get_json(url, timeout=8):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 MarsAI-Console"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_crypto():
    syms = {
        "BTC-5M": "BTCUSDT", "ETH-1H": "ETHUSDT", "SOL-15M": "SOLUSDT",
        "MATIC-30M": "MATICUSDT", "DOGE-5M": "DOGEUSDT", "XRP-5M": "XRPUSDT"
    }
    try:
        url = "https://api.binance.com/api/v3/ticker/price?symbols=" + json.dumps(list(syms.values())).replace(" ", "")
        data = http_get_json(url)
        m = {d["symbol"]: float(d["price"]) for d in data}
        out = {k: m[v] for k, v in syms.items() if v in m}
        if "MATIC-30M" not in out:  # MATIC 已迁移为 POL
            try:
                p = http_get_json("https://api.binance.com/api/v3/ticker/price?symbol=POLUSDT")
                out["MATIC-30M"] = float(p["price"]) * 4.0
            except Exception:
                pass
        return out, "binance"
    except Exception:
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin,ethereum,solana,polygon,dogecoin,ripple&vs_currencies=usd"
            d = http_get_json(url)
            return {
                "BTC-5M": d["bitcoin"]["usd"], "ETH-1H": d["ethereum"]["usd"],
                "SOL-15M": d["solana"]["usd"], "MATIC-30M": d["polygon"]["usd"],
                "DOGE-5M": d["dogecoin"]["usd"], "XRP-5M": d["ripple"]["usd"],
            }, "coingecko"
        except Exception:
            return {}, "failed"


def fetch_us_stocks():
    syms = {
        "英伟达[NVIDIA]": "NVDA", "特斯拉[TSLA]": "TSLA",
        "AAPL": "AAPL", "MSFT": "MSFT", "GOOGL": "GOOGL", "AMZN": "AMZN"
    }
    out = {}
    try:
        for k, code in syms.items():
            url = f"https://qt.gtimg.cn/q=us{code}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            with urllib.request.urlopen(req, timeout=8) as r:
                raw = r.read().decode("gbk")
            parts = raw.split("=")[1].strip('"').split("~")
            if len(parts) > 3 and parts[3]:
                out[k] = float(parts[3])
        return out, "tencent-gtimg"
    except Exception:
        return {}, "failed"


def fetch_gold():
    try:
        d = http_get_json("https://api.gold-api.com/price/XAU")
        return {"黄金[XAU]": float(d["price"])}, "gold-api"
    except Exception:
        return {}, "failed"


def fetch_fx():
    try:
        d = http_get_json("https://open.er-api.com/v6/latest/USD")
        return {"美元": float(d["rates"]["CNY"])}, "exchangerate"
    except Exception:
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
        except Exception:
            pass
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
