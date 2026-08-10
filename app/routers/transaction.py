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
async def send_transaction(send_transaction_request: SendTransactionRequest):
    try:
        url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/SendTransaction"
        sender = await get_sender_from_db()
        payload = await build_lightremit_payload(send_transaction_request, sender)
        body = send_transaction_request.model_dump(by_alias=True)
        body["agentSessionId"] = ""
        signature, body =  build_request("POST", url,body)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=body,
                headers={"Authorization": signature}
            )

        raw = response.json()

        if raw.get("code") == "0":
            status = "success"
            message = "Transaction accepted"
            data = SendTransactionResponseSuccess.model_validate(raw)
        else:
            status = "error"
            message = f"code {raw.get('code')} from third party with message: {raw.get('message', '')}"
            data = ErrorItems.model_validate(raw)

        return BaseResponse(status=status, message=message, data=data)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    