from fastapi import APIRouter

from app.schemas import RateRequest

router = APIRouter()

@router.post("/send_transaction", response_model=BaseResponse[RateItem])
async def send_transaction(rate_request: RateRequest):
    