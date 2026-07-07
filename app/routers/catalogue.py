from http.client import HTTPException

from fastapi import APIRouter
import httpx
from sqlalchemy import null

from app.schemas import BankItem, BankRequest, BaseResponse, CatalogueItem, CatalogueRequest, ErrorItems, ExchangeRateItem, RateItem, RateItemSuccess, RateRequest, ResponseSchema
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
            "data": response.json().get("locationDetail") or []
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/get_exchange_rate", response_model=ResponseSchema[list[ExchangeRateItem]])
async def get_exchange_rate():
    try:
        url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetEXRateList"
        signature, body =  build_request("POST", url, {
            "agentSessionId": ""
        })
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=body,
                headers={"Authorization": signature}
            )
            
            status = ""
            message = ""
            # if(response.json().get("code") == "0"):
            #     status = "success"
            #     message = "Data fetched successfully"
            # elif(response.json().get("code") != "0"):
            #     status = "error"
            #     message = f"code {response.json().get('code')} from third party with message: {response.json().get('message', '')}"
        
        return {
            "status": status,
            "message": message,
            "data": response.json() or [] 
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/get_rate", response_model=BaseResponse[RateItem])
async def get_rate(rate_request: RateRequest):
    try:
        url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetEXRate"
        signature, body =  build_request("POST", url, {
            "agentSessionId": "",
            "transferAmount": rate_request.transfer_amount,
            "calcBy": rate_request.calc_by,
            "payoutCurrency": rate_request.payout_currency,
            "paymentMode": rate_request.payment_mode,
            "locationId": rate_request.location_id,
            "payoutCountry": rate_request.payout_country
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
                data = RateItemSuccess.model_validate(response.json())
            elif(response.json().get("code") != "0"):
                status = "error"
                message = f"code {response.json().get('code')} from third party with message: {response.json().get('message', '')}"
                data = ErrorItems.model_validate(response.json())
        
        return BaseResponse(status=status, message=message, data=data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))