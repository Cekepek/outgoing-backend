from fastapi import HTTPException
from fastapi import APIRouter
import httpx
from app.schemas import ConnectionBase, ResponseSchema
from app.config import settings

from app.utils.signature import generate_signature

router = APIRouter()


@router.get("/check_connection")
async def check_connection():
    try:
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
    except HTTPException:
        raise
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/check_connection_transferku")
async def check_connection_transferku():
    try:
        url = f"{settings.payment_host_transferku}/v1/connection"
        async with httpx.AsyncClient() as client:
            response = await client.get(
                url,
                auth=httpx.BasicAuth(settings.payment_client_id_transferku, settings.payment_client_secret_transferku)
            )
            return response.json()
    except HTTPException:
        raise
    except Exception as e:
        print(e)
        raise HTTPException(status_code=500, detail=str(e))
    
