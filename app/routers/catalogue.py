from http.client import HTTPException

from fastapi import APIRouter
import httpx

from app.schemas import BankItem, BankRequest, CatalogueItem, CatalogueRequest, ResponseSchema
from app.config import settings
from app.utils.signature import build_request

router = APIRouter()

@router.post("/get_catalogue", response_model=ResponseSchema[list[CatalogueItem]])
async def get_catalogue(catalogue_request: CatalogueRequest):
    try:
        url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetCatalogue"
        signature, body =  build_request("POST", url, {
            "agentSessionId": "",
            "catalogueType": catalogue_request.catalogueType,
            "additionalField1": catalogue_request.additionalField1,
            "additionalField2": catalogue_request.additionalField2,
            "additionalField3": catalogue_request.additionalField3
        })
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=body,
                headers={"Authorization": signature}
            )
            
            status = ""
            message = ""
            if(response.json().get("code") == "0"):
                status = "success"
                message = "Data fetched successfully"
            elif(response.json().get("code") != "0"):
                
                status = "error"
                message = f"code {response.json().get('code')} from third party with message: {response.json().get('message', '')}"
                
            return {
                "status": status,
                "message": message,
                "data": response.json().get("result", [])
            }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/get_bank", response_model=ResponseSchema[list[BankItem]])
async def get_bank(bank_request: BankRequest):
    try:
        url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetAgentList"
        signature, body =  build_request("POST", url, {
            "agentSessionId": "",
            "paymentMode": "B",
            "payoutCountry": bank_request.payoutCountry
        })
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=body,
                headers={"Authorization": signature}
            )
            
            status = ""
            message = ""
            if(response.json().get("code") == "0"):
                status = "success"
                message = "Data fetched successfully"
            elif(response.json().get("code") != "0"):
                status = "error"
                message = f"code {response.json().get('code')} from third party with message: {response.json().get('message', '')}"
        
        return {
            "status": status,
            "message": message,
            "data": response.json().get("locationDetail", [])
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))