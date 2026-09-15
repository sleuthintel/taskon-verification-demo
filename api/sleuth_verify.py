"""Verify TaskOn completion via $SLEUTH Uniswap swap volume on Robinhood Chain."""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from typing import Any

SLEUTH_TOKEN = os.environ.get(
    'SLEUTH_TOKEN_ADDRESS',
    '0x0500a1a597A631CEc9637767f7B75c5B5d0c1dB4',
).lower()
# Uniswap v4 on Robinhood Chain routes through PoolManager.
UNISWAP_POOL_MANAGER = os.environ.get(
    'SLEUTH_UNISWAP_POOL_MANAGER',
    '0x8366a39CC670B4001A1121B8F6A443A643e40951',
).lower()
SLEUTH_PAIR_ID = os.environ.get(
    'SLEUTH_PAIR_ID',
    '0x6f8b82d7506468eda9b86e62d2223c29af86a074f8073f1abd43083be874fa3b',
).lower()
BLOCKSCOUT_BASE = os.environ.get(
    'BLOCKSCOUT_API_BASE',
    'https://robinhoodchain.blockscout.com/api/v2',
).rstrip('/')
DEXSCREENER_PAIR_URL = (
    'https://api.dexscreener.com/latest/dex/pairs/robinhood/'
    + urllib.parse.quote(SLEUTH_PAIR_ID, safe='')
)
MIN_VOLUME_USD = float(os.environ.get('SLEUTH_MIN_SWAP_VOLUME_USD', '100'))
MAX_PAGES = int(os.environ.get('SLEUTH_TRANSFER_MAX_PAGES', '15'))

_WALLET_RE = re.compile(r'^0x[a-fA-F0-9]{40}$')
_HTTP_HEADERS = {
    'Accept': 'application/json',
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    ),
    'Referer': 'https://robinhoodchain.blockscout.com/',
    'Origin': 'https://robinhoodchain.blockscout.com',
}


def _http_json(url: str, timeout: float = 25.0) -> Any:
    req = urllib.request.Request(url, headers=_HTTP_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _sleuth_price_usd() -> float:
    try:
        body = _http_json(DEXSCREENER_PAIR_URL, timeout=15)
        pairs = body if isinstance(body, list) else body.get('pairs') or []
        for pair in pairs:
            if not isinstance(pair, dict):
                continue
            if str(pair.get('pairAddress') or '').lower() != SLEUTH_PAIR_ID:
                continue
            price = pair.get('priceUsd')
            if price is not None:
                return float(price)
        if pairs and isinstance(pairs[0], dict) and pairs[0].get('priceUsd') is not None:
            return float(pairs[0]['priceUsd'])
    except Exception:
        pass
    return 0.0


def _transfer_usd(item: dict[str, Any], fallback_price: float) -> float:
    token = item.get('token') if isinstance(item.get('token'), dict) else {}
    total = item.get('total') if isinstance(item.get('total'), dict) else {}
    raw = total.get('value')
    decimals = int(total.get('decimals') or token.get('decimals') or 18)
    if raw in (None, ''):
        return 0.0
    amount = int(raw) / (10 ** decimals)
    rate = token.get('exchange_rate')
    price = float(rate) if rate not in (None, '') else fallback_price
    if price <= 0:
        return 0.0
    return amount * price


def _is_uniswap_sleuth_swap(item: dict[str, Any], wallet: str) -> bool:
    token = item.get('token') if isinstance(item.get('token'), dict) else {}
    if str(token.get('address_hash') or token.get('address') or '').lower() != SLEUTH_TOKEN:
        return False
    from_addr = str(((item.get('from') or {}).get('hash')) or '').lower()
    to_addr = str(((item.get('to') or {}).get('hash')) or '').lower()
    if wallet not in (from_addr, to_addr):
        return False
    pool_side = UNISWAP_POOL_MANAGER
    if from_addr == pool_side or to_addr == pool_side:
        return True
    for side in (item.get('from'), item.get('to')):
        if not isinstance(side, dict):
            continue
        name = str(side.get('name') or '').lower()
        if 'uniswap' in name or 'poolmanager' in name:
            return True
    return False


def wallet_swap_volume_usd(wallet: str) -> tuple[float, int]:
    """Return (total_usd, swap_count) for $SLEUTH Uniswap swaps on Robinhood Chain."""
    wallet = wallet.lower()
    fallback_price = _sleuth_price_usd()
    total_usd = 0.0
    swap_count = 0
    url = (
        f'{BLOCKSCOUT_BASE}/addresses/{wallet}/token-transfers'
        f'?token={SLEUTH_TOKEN}'
    )
    pages = 0
    while url and pages < MAX_PAGES:
        pages += 1
        body = _http_json(url)
        items = body.get('items') if isinstance(body, dict) else None
        if not isinstance(items, list):
            break
        for item in items:
            if not isinstance(item, dict):
                continue
            if not _is_uniswap_sleuth_swap(item, wallet):
                continue
            usd = _transfer_usd(item, fallback_price)
            if usd <= 0:
                continue
            total_usd += usd
            swap_count += 1
        next_params = body.get('next_page_params') if isinstance(body, dict) else None
        if not isinstance(next_params, dict) or not next_params:
            break
        qs = urllib.parse.urlencode({k: str(v) for k, v in next_params.items()})
        url = f'{BLOCKSCOUT_BASE}/addresses/{wallet}/token-transfers?token={SLEUTH_TOKEN}&{qs}'
    return round(total_usd, 2), swap_count


def qualifies_wallet(address: str) -> bool:
    if not _WALLET_RE.match((address or '').strip()):
        return False
    volume_usd, _ = wallet_swap_volume_usd(address.strip())
    return volume_usd >= MIN_VOLUME_USD
