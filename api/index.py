from fastapi import FastAPI, Header
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
from pydantic import BaseModel

from sleuth_verify import MIN_VOLUME_USD, qualifies_wallet, wallet_swap_volume_usd

app = FastAPI(
    title="Sleuth Intel TaskOn Verification API",
    description="Verify $SLEUTH Uniswap swap volume on Robinhood Chain for TaskOn tasks",
    version="1.1.0",
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
    summary="Verify Task Completion",
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
                },
                error=None,
            )
        return VerificationResponse(result={"isValid": is_valid}, error=None)
    except Exception:
        return VerificationResponse(
            result={"isValid": False},
            error="verification temporarily unavailable",
        )


@app.get("/")
async def root():
    return {
        "message": "Sleuth Intel TaskOn verification API",
        "rule": f">= ${MIN_VOLUME_USD:g} $SLEUTH swap volume on Uniswap (Robinhood Chain)",
        "endpoint": "/api/task/verification?address=0x...",
    }
