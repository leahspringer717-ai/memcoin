
import requests
DEX_API = "https://api.dexscreener.com/latest/dex/tokens/"

def get_usdt_pair(token_address):
    try:
        r = requests.get(DEX_API + token_address, timeout=10)
        data = r.json()
        for p in data.get("pairs", []):
            if p.get("quoteToken", {}).get("symbol") == "USDT":
                return {
                    "symbol": p["baseToken"]["symbol"],
                    "name": p["baseToken"]["name"],
                    "price": float(p.get("priceUsd", 0)),
                    "volume": float(p["volume"].get("h1", 0)),
                    "change": float(p["priceChange"].get("h1", 0)),
                    "liquidity": float(p["liquidity"].get("usd", 0)),
                    "dex": p.get("dexId"),
                }
    except:
        pass
    return None
