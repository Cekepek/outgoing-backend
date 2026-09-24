from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Sender, User
from app.schemas import (
    BaseResponse,
    ErrorItems,
    SendTransactionRequest,
    SendTransactionResponse,
    SendTransactionResponseSuccess,
)
from app.services.apiService import get_current_user
from app.services.transactionServices import send_transaction as process_send_transaction

router = APIRouter()


async def get_sender_from_db(db: Session, sender_id: int) -> Sender:
    sender = db.query(Sender).filter(Sender.id == sender_id).first()
    if not sender:
        raise HTTPException(status_code=404, detail="Sender not found")
    return sender


@router.post("/send_transaction", response_model=BaseResponse[SendTransactionResponse])
async def send_transaction(
    send_transaction_request: SendTransactionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        if not current_user.sender:
            raise HTTPException(status_code=400, detail="No sender profile linked to this account")
        sender = await get_sender_from_db(db, current_user.sender.id)

        raw, agent_used = await process_send_transaction(
            req=send_transaction_request,
            sender=sender,
        )

        if agent_used == "TRANSFERKU":
            return BaseResponse(
                status="success",
                message="Transaction accepted by Transferku",
                data=raw,
            )

        if raw.get("code") == "0":
            return BaseResponse(
                status="success",
                message="Transaction accepted",
                data=SendTransactionResponseSuccess.model_validate(raw),
            )
        return BaseResponse(
            status="error",
            message=f"code {raw.get('code')} from third party with message: {raw.get('message', '')}",
            data=ErrorItems.model_validate(raw),
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"[ERROR send_transaction] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

    