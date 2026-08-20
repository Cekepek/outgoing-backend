from app.utils.signature import generate_agent_txn_id
from app.services.apiService import get_current_user
from app.models import User
from sqlalchemy.sql.functions import current_user
from app.database import get_db
from app.services.apiService import get_current_session
from fastapi import Depends
from app.models import SessionModel
from app.models import Sender
from sqlalchemy.orm import Session
from app.services.schemasService import build_lightremit_payload
from fastapi import APIRouter, HTTPException
import httpx
from app.config import settings
from app.schemas import BaseResponse, ErrorItems, RateRequest, SendTransactionRequest, SendTransactionResponse, SendTransactionResponseSuccess
from app.utils.signature import build_request

router = APIRouter()

async def get_sender_from_db(db: Session, sender_id: int) -> Sender:
    sender = db.query(Sender).filter(Sender.id == sender_id).first()
    if not sender:
        raise HTTPException(status_code=404, detail="Sender not found")
    return sender

@router.post("/send_transaction", response_model=BaseResponse[SendTransactionResponse])
async def send_transaction(send_transaction_request: SendTransactionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)):
    try:
        if not current_user.sender:
            raise HTTPException(status_code=400, detail="No sender profile linked to this account")
        sender = current_user.sender
        url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/SendTransaction"
        sender = await get_sender_from_db(db, sender.id)
        agent_txn_id = generate_agent_txn_id()
        agent_session_id = ""
        payload = await build_lightremit_payload(send_transaction_request, sender, agent_session_id, agent_txn_id)
        print(payload)
        signature, payload = build_request("POST", url, payload.model_dump(by_alias=True))

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(url, json=payload, headers={"Authorization": signature})

        try:
            raw = response.json()
            # print(raw)
        except ValueError:
            # print(raw)
            raise HTTPException(status_code=502, detail="Invalid response from payment provider")

        if raw.get("code") == "0":
            print(SendTransactionResponseSuccess.model_validate(raw))
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
        print(e)
        raise HTTPException(status_code=500, detail=str(e))
    