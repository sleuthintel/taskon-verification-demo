"""Verify TaskOn completion via $SLEUTH Uniswap swap volume on Robinhood Chain."""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

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
LEADERBOARD_MAX_PAGES = int(os.environ.get('SLEUTH_LEADERBOARD_MAX_PAGES', '40'))
LEADERBOARD_DEFAULT_LIMIT = int(os.environ.get('SLEUTH_LEADERBOARD_LIMIT', '50'))
DAY_TIMEZONE = os.environ.get('SLEUTH_DAY_TIMEZONE', 'UTC')

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

_leaderboard_cache: dict[str, Any] = {}


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


def _parse_transfer_ts(item: dict[str, Any]) -> datetime | None:
    raw = item.get('timestamp')
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
    except ValueError:
        return None


def _current_day_key() -> str:
    tz = ZoneInfo(DAY_TIMEZONE)
    return datetime.now(tz).strftime('%Y-%m-%d')


def _day_start_utc() -> datetime:
    tz = ZoneInfo(DAY_TIMEZONE)
    now_local = datetime.now(tz)
    start_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_local.astimezone(timezone.utc)


def _is_sleuth_transfer(item: dict[str, Any]) -> bool:
    token = item.get('token') if isinstance(item.get('token'), dict) else {}
    addr = str(token.get('address_hash') or token.get('address') or '').lower()
    return addr == SLEUTH_TOKEN


def _addr_hash(side: Any) -> str:
    if not isinstance(side, dict):
        return ''
    return str(side.get('hash') or '').lower()


def _is_uniswap_side(side: Any) -> bool:
    addr = _addr_hash(side)
    if addr == UNISWAP_POOL_MANAGER:
        return True
    if not isinstance(side, dict):
        return False
    name = str(side.get('name') or '').lower()
    return 'uniswap' in name or 'poolmanager' in name


def _wallet_in_uniswap_swap(item: dict[str, Any]) -> str | None:
    from_addr = _addr_hash(item.get('from'))
    to_addr = _addr_hash(item.get('to'))
    if _is_uniswap_side(item.get('from')):
        return to_addr or None
    if _is_uniswap_side(item.get('to')):
        return from_addr or None
    return None


def _is_uniswap_sleuth_transfer(item: dict[str, Any]) -> bool:
    if not _is_sleuth_transfer(item):
        return False
    return _wallet_in_uniswap_swap(item) is not None


def _is_uniswap_sleuth_swap(item: dict[str, Any], wallet: str) -> bool:
    if not _is_uniswap_sleuth_transfer(item):
        return False
    swap_wallet = _wallet_in_uniswap_swap(item)
    return swap_wallet == wallet.lower()


def _paginate_transfers(
    start_url: str,
    *,
    max_pages: int,
    since_utc: datetime | None,
    on_item,
) -> None:
    url = start_url
    pages = 0
    while url and pages < max_pages:
        pages += 1
        body = _http_json(url)
        items = body.get('items') if isinstance(body, dict) else None
        if not isinstance(items, list):
            break

        stop = False
        for item in items:
            if not isinstance(item, dict):
                continue
            if since_utc is not None:
                ts = _parse_transfer_ts(item)
                if ts is not None and ts < since_utc:
                    stop = True
                    break
            on_item(item)

        if stop:
            break

        next_params = body.get('next_page_params') if isinstance(body, dict) else None
        if not isinstance(next_params, dict) or not next_params:
            break
        base = start_url.split('?', 1)[0]
        qs = urllib.parse.urlencode({k: str(v) for k, v in next_params.items()})
        token_qs = f'token={SLEUTH_TOKEN}'
        url = f'{base}?{token_qs}&{qs}' if 'token=' in start_url else f'{base}?{qs}'


def wallet_swap_volume_usd(
    wallet: str,
    *,
    since_utc: datetime | None = None,
) -> tuple[float, int]:
    """Return (total_usd, swap_count) for $SLEUTH Uniswap swaps on Robinhood Chain."""
    wallet = wallet.lower()
    fallback_price = _sleuth_price_usd()
    total_usd = 0.0
    swap_count = 0
    start_url = (
        f'{BLOCKSCOUT_BASE}/addresses/{wallet}/token-transfers'
        f'?token={SLEUTH_TOKEN}'
    )

    def handle(item: dict[str, Any]) -> None:
        nonlocal total_usd, swap_count
        if not _is_uniswap_sleuth_swap(item, wallet):
            return
        usd = _transfer_usd(item, fallback_price)
        if usd <= 0:
            return
        total_usd += usd
        swap_count += 1

    _paginate_transfers(start_url, max_pages=MAX_PAGES, since_utc=since_utc, on_item=handle)
    return round(total_usd, 2), swap_count


def wallet_daily_swap_volume_usd(wallet: str) -> tuple[float, int]:
    return wallet_swap_volume_usd(wallet, since_utc=_day_start_utc())


def qualifies_wallet(address: str) -> bool:
    if not _WALLET_RE.match((address or '').strip()):
        return False
    volume_usd, _ = wallet_swap_volume_usd(address.strip())
    return volume_usd >= MIN_VOLUME_USD


def qualifies_wallet_daily(address: str) -> bool:
    if not _WALLET_RE.match((address or '').strip()):
        return False
    volume_usd, _ = wallet_daily_swap_volume_usd(address.strip())
    return volume_usd >= MIN_VOLUME_USD


def _build_daily_leaderboard_entries() -> list[dict[str, Any]]:
    fallback_price = _sleuth_price_usd()
    since_utc = _day_start_utc()
    totals: dict[str, dict[str, float | int]] = {}
    start_url = f'{BLOCKSCOUT_BASE}/tokens/{SLEUTH_TOKEN}/transfers'

    def handle(item: dict[str, Any]) -> None:
        if not _is_uniswap_sleuth_transfer(item):
            return
        wallet = _wallet_in_uniswap_swap(item)
        if not wallet or not _WALLET_RE.match(wallet):
            return
        usd = _transfer_usd(item, fallback_price)
        if usd <= 0:
            return
        bucket = totals.setdefault(wallet, {'volumeUsd': 0.0, 'swapCount': 0})
        bucket['volumeUsd'] = float(bucket['volumeUsd']) + usd
        bucket['swapCount'] = int(bucket['swapCount']) + 1

    _paginate_transfers(
        start_url,
        max_pages=LEADERBOARD_MAX_PAGES,
        since_utc=since_utc,
        on_item=handle,
    )

    entries = []
    for rank, (wallet, stats) in enumerate(
        sorted(totals.items(), key=lambda row: float(row[1]['volumeUsd']), reverse=True),
        start=1,
    ):
        volume = round(float(stats['volumeUsd']), 2)
        entries.append(
            {
                'rank': rank,
                'address': wallet,
                'volumeUsd': volume,
                'swapCount': int(stats['swapCount']),
                'qualified': volume >= MIN_VOLUME_USD,
            }
        )
    return entries


def refresh_daily_leaderboard(*, force: bool = False) -> dict[str, Any]:
    day_key = _current_day_key()
    if (
        not force
        and _leaderboard_cache.get('dayKey') == day_key
        and isinstance(_leaderboard_cache.get('entries'), list)
    ):
        return _leaderboard_cache

    entries = _build_daily_leaderboard_entries()
    payload = {
        'dayKey': day_key,
        'timezone': DAY_TIMEZONE,
        'minVolumeUsd': MIN_VOLUME_USD,
        'builtAt': datetime.now(timezone.utc).isoformat(),
        'entries': entries,
        'qualifiedCount': sum(1 for row in entries if row.get('qualified')),
    }
    _leaderboard_cache.clear()
    _leaderboard_cache.update(payload)
    return payload


def get_daily_leaderboard(limit: int | None = None) -> dict[str, Any]:
    payload = refresh_daily_leaderboard()
    cap = limit if limit is not None else LEADERBOARD_DEFAULT_LIMIT
    cap = max(1, min(cap, 500))
    return {
        'dayKey': payload['dayKey'],
        'timezone': payload['timezone'],
        'minVolumeUsd': payload['minVolumeUsd'],
        'builtAt': payload['builtAt'],
        'qualifiedCount': payload['qualifiedCount'],
        'entries': payload['entries'][:cap],
    }
