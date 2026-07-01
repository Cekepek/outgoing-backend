from fastapi import APIRouter
import httpx
from app.schemas import ConnectionBase, ResponseSchema
from app.config import settings

from app.utils.signature import generate_signature

router = APIRouter()


@router.get("/check_connection")
async def check_connection():
    url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetEcho"

    # timestamp is also used as agentSessionId (same as Postman's pm.environment.set("agentSessionId", requestTimeStamp))
    signature, agent_session_id = generate_signature(
        method="POST",
        url=url,
        body={"agentSessionId": 0}  # placeholder, replaced below
    )

    body = {"agentSessionId": agent_session_id}

    # Regenerate signature with correct body
    signature, agent_session_id = generate_signature(
        method="POST",
        url=url,
        body=body
    )

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url,
            json=body,
            headers={"Authorization": signature}
        )
        return response.json()