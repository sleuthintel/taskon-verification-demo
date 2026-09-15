import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from pydantic import BaseModel

from sleuth_verify import (
    DAY_TIMEZONE,
    MIN_VOLUME_USD,
    _current_day_key,
    get_daily_leaderboard,
    qualifies_wallet,
    qualifies_wallet_daily,
    refresh_daily_leaderboard,
    wallet_daily_swap_volume_usd,
    wallet_swap_volume_usd,
)

app = FastAPI(
    title="Sleuth Intel TaskOn Verification API",
    description="Verify $SLEUTH Uniswap swap volume on Robinhood Chain for TaskOn tasks",
    version="1.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class VerificationResponse(BaseModel):
    result: dict = {"isValid": bool}
    error: Optional[str] = None


@app.get(
    "/api/task/verification",
    response_model=VerificationResponse,
    summary="Verify Task Completion (all-time)",
    description=(
        "Returns isValid=true when the wallet has at least "
        f"${MIN_VOLUME_USD:g} cumulative $SLEUTH swap volume on Uniswap (Robinhood Chain)."
    ),
)
async def verify_task(
    address: str,
    authorization: Optional[str] = Header(None),
    debug: Optional[int] = 0,
) -> VerificationResponse:
    raw = (address or '').strip()
    if not raw:
        return VerificationResponse(result={"isValid": False}, error="address required")

    try:
        is_valid = qualifies_wallet(raw)
        if debug:
            volume_usd, swaps = wallet_swap_volume_usd(raw)
            return VerificationResponse(
                result={
                    "isValid": is_valid,
                    "volumeUsd": volume_usd,
                    "swapCount": swaps,
                    "minVolumeUsd": MIN_VOLUME_USD,
                    "period": "all-time",
                },
                error=None,
            )
        return VerificationResponse(result={"isValid": is_valid}, error=None)
    except Exception:
        return VerificationResponse(
            result={"isValid": False},
            error="verification temporarily unavailable",
        )


@app.get(
    "/api/task/verification/daily",
    response_model=VerificationResponse,
    summary="Verify Task Completion (daily)",
    description=(
        "Returns isValid=true when the wallet has at least "
        f"${MIN_VOLUME_USD:g} $SLEUTH swap volume on Uniswap today "
        f"(calendar day in {DAY_TIMEZONE}). Resets at midnight."
    ),
)
async def verify_task_daily(
    address: str,
    authorization: Optional[str] = Header(None),
    debug: Optional[int] = 0,
) -> VerificationResponse:
    raw = (address or '').strip()
    if not raw:
        return VerificationResponse(result={"isValid": False}, error="address required")

    try:
        is_valid = qualifies_wallet_daily(raw)
        if debug:
            volume_usd, swaps = wallet_daily_swap_volume_usd(raw)
            return VerificationResponse(
                result={
                    "isValid": is_valid,
                    "volumeUsd": volume_usd,
                    "swapCount": swaps,
                    "minVolumeUsd": MIN_VOLUME_USD,
                    "period": "daily",
                    "dayKey": _current_day_key(),
                    "timezone": DAY_TIMEZONE,
                },
                error=None,
            )
        return VerificationResponse(result={"isValid": is_valid}, error=None)
    except Exception:
        return VerificationResponse(
            result={"isValid": False},
            error="verification temporarily unavailable",
        )


@app.get("/api/leaderboard/daily")
async def daily_leaderboard(limit: Optional[int] = None, refresh: Optional[int] = 0):
    try:
        if refresh:
            refresh_daily_leaderboard(force=True)
        return get_daily_leaderboard(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="leaderboard temporarily unavailable") from exc


@app.get("/api/cron/refresh-leaderboard")
async def cron_refresh_leaderboard(authorization: Optional[str] = Header(None)):
    secret = os.environ.get('CRON_SECRET', '').strip()
    if secret:
        token = (authorization or '').removeprefix('Bearer ').strip()
        if token != secret:
            raise HTTPException(status_code=401, detail="unauthorized")
    try:
        return refresh_daily_leaderboard(force=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="leaderboard refresh failed") from exc


@app.get("/")
async def root():
    return {
        "message": "Sleuth Intel TaskOn verification API",
        "endpoints": {
            "allTimeVerification": "/api/task/verification?address=0x...",
            "dailyVerification": "/api/task/verification/daily?address=0x...",
            "dailyLeaderboard": "/api/leaderboard/daily",
        },
        "rules": {
            "allTime": f">= ${MIN_VOLUME_USD:g} cumulative $SLEUTH Uniswap swap volume",
            "daily": f">= ${MIN_VOLUME_USD:g} $SLEUTH Uniswap swap volume per calendar day ({DAY_TIMEZONE})",
        },
    }
