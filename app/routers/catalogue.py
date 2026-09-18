from app.services.catalogueServices import (
    best_bank_for_country,
    fetch_locations_from_both,
    get_direct_rate,
    get_transferku_business_relation,
    get_transferku_purpose_of_remittance,
    get_transferku_relation,
    get_transferku_source_of_fund,
)
from typing import Any, Optional
from fastapi import APIRouter, HTTPException, Body
import httpx
from sqlalchemy import null

from app.schemas import (
    BankItem,
    BankRequest,
    BaseResponse,
    CatalogueItem,
    CatalogueRequest,
    ErrorItems,
    ExchangeRateItem,
    LocationRequest,
    RateItem,
    RateItemSuccess,
    RateRequest,
    ResponseSchema,
    TransferkuCatalogueItem,
    TransferkuPurposeRequest,
    TransferkuRelationRequest,
    TransferkuSourceOfFundRequest,
)
from app.config import settings
from app.utils.signature import build_request

router = APIRouter()
COUNTRY_NAMES = {
    "AUS": "Australia",
    "CHN": "China",
    "EUR": "Eropa",
    "GBR": "Inggris",
    "HKG": "Hong Kong",
    "MYS": "Malaysia",
    "PHL": "Filipina",
    "SGP": "Singapura",
    "THA": "Thailand",
}
CURRENCY_NAME = {
    "AUS": "AUD",
    "CHN": "CNY",
    "EUR": "EUR",
    "GBR": "GBP",
    "HKG": "HKD",
    "MYS": "MYR",
    "PHL": "PHP",
    "SGP": "SGD",
    "THA": "THB",
}
BANK_NAME = {
    "AUS": "AUSALL",
    "CHN": "CHNBAN",
    "EUR": "EURALL01",
    "GBR": "GBRALL",
    "HKG": "HKGABN",
    "MYS": "MYSAFF",
    "PHL": "PHLALLBA",
    "SGP": "SGPALL",
    "THA": "THABAN01",
}

def _best_bank_for(country_code: str) -> str | None:
    banks = BANK_NAME.get(country_code)  # e.g. list of candidate banks for this country
    if not banks:
        return None

    rates = {
        bank: get_exchange_rate(bank, country_code)
        for bank in banks
    }
    # pick bank with the best (e.g. highest) rate — adjust comparison to your business logic
    return max(rates, key=rates.get)

def enrich_catalogue(catalogue_type: str, raw_result: list[dict]) -> list[dict]:
    if catalogue_type == "CTY":
        return [
            {
                "data": item["data"],
                "value": item["value"],
                "label": COUNTRY_NAMES.get(item["value"], item["value"]),
                "currency": CURRENCY_NAME.get(item["value"], item["value"]),
                "bank": BANK_NAME.get(item["value"], item["value"]),
            }
            for item in raw_result
        ]

    # Unknown/unmapped catalogueType — pass through raw, don't crash
    return raw_result
@router.post("/get_catalogue", response_model=ResponseSchema[list[dict[str, Any]]])
async def get_catalogue(catalogue_request: CatalogueRequest):
    try:
        url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetCatalogue"
        signature, body = build_request("POST", url, {
            "agentSessionId": "",
            "catalogueType": catalogue_request.catalogueType,
            "additionalField1": catalogue_request.additionalField1,
            "additionalField2": catalogue_request.additionalField2,
            "additionalField3": catalogue_request.additionalField3,
        })

        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=body, headers={"Authorization": signature})

        payload = response.json()  # parse once

        if payload.get("code") == "0":
            status = "success"
            message = "Data fetched successfully"
        else:
            status = "error"
            message = f"code {payload.get('code')} from third party with message: {payload.get('message', '')}"

        raw_result = payload.get("result", [])
        enriched_data = enrich_catalogue(catalogue_request.catalogueType, raw_result)

        return {
            "status": status,
            "message": message,
            "data": enriched_data,
        }
    except Exception as e:
        print(f"[ERROR get_catalogue] {type(e).__name__}: {e}")
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
            
        payload = response.json()
        status = ""
        message = ""
        if payload.get("code") == "0":
            status = "success"
            message = "Data fetched successfully"
        else:
            status = "error"
            message = f"code {payload.get('code')} from third party with message: {payload.get('message', '')}"
        
        raw_locations = payload.get("locationDetail") or []
        banks = [
            {
                "value": loc.get("locationId") or loc.get("value"),
                "description": loc.get("locationName") or loc.get("description"),
                "optionalField": loc.get("optionalField", ""),
            }
            for loc in raw_locations
        ]

        return {
            "status": status,
            "message": message,
            "data": banks
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/get_exchange_rate", response_model=ResponseSchema[list[ExchangeRateItem]])
async def get_exchange_rate():
    url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetEXRateList"
    signature, body = build_request("POST", url, {"agentSessionId": ""})

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=body, headers={"Authorization": signature})
            response.raise_for_status()
            payload = response.json()

        code = payload.get("code")
        if code == "0":
            status, message = "success", "Data fetched successfully"
        else:
            status = "error"
            message = f"code {code} from third party with message: {payload.get('message', '')}"

        return {
            "status": status,
            "message": message,
            "data": payload.get("data") or [],  # adjust key to match LightRemit's actual response shape
        }

    except httpx.HTTPStatusError as e:
        print(f"[ERROR get_exchange_rate] HTTP {e.response.status_code}: {e.response.text}")
        raise HTTPException(status_code=502, detail="Upstream exchange rate service error")
    except Exception as e:
        print(f"[ERROR get_exchange_rate] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/get_rate", response_model=BaseResponse[dict[str, Any]])
async def get_rate(rate_request: RateRequest):
    try:
        result, error_message = await get_direct_rate(
            payout_country=rate_request.payout_country,
            payout_currency=rate_request.payout_currency,
            transfer_amount=rate_request.transfer_amount,
            calc_by=rate_request.calc_by or "P",
            payment_mode=rate_request.payment_mode or "B",
            location_id=rate_request.location_id,
            location_name=rate_request.location_name,
            payer_id=rate_request.payer_id,
            optional_field=rate_request.optional_field,
            transaction_type=rate_request.transaction_type,
        )
        if result is not None:
            return BaseResponse(
                status="success",
                message="Data fetched successfully",
                data=result,
            )
        else:
            return BaseResponse(
                status="error",
                message=error_message or "Data not found",
                data={},
            )
    except Exception as e:
        print(f"[ERROR get_rate] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/get_transferku_purpose", response_model=BaseResponse[list[dict[str, str]]])
@router.post("/get_transferku_catalogue", response_model=BaseResponse[list[dict[str, str]]])
async def get_transferku_purpose(req: TransferkuPurposeRequest):
    try:
        purposes, error_message = await get_transferku_purpose_of_remittance(
            iso_code=req.iso_code,
            payer_id=req.payer_id,
            transaction_type=req.transaction_type,
        )
        if purposes is not None:
            return BaseResponse(
                status="success",
                message="Data fetched successfully",
                data=purposes,
            )
        else:
            return BaseResponse(
                status="error",
                message=error_message or "Data not found",
                data=[],
            )
    except Exception as e:
        print(f"[ERROR get_transferku_purpose] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/get_locations", response_model=BaseResponse[dict[str, Any]])
async def get_locations(req: LocationRequest):
    try:
        data = await fetch_locations_from_both(
            iso_code=req.iso_code,
            payment_mode=req.payment_mode or "B",
            transaction_type=req.transaction_type,
        )
        return BaseResponse(
            status="success",
            message="Locations fetched successfully",
            data=data,
        )
    except Exception as e:
        print(f"[ERROR get_locations] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# @router.post("/get_transferku_relation", response_model=BaseResponse[list[dict[str, str]]])
# @router.post("/get_transferku_relations", response_model=BaseResponse[list[dict[str, str]]])
# async def get_transferku_relation_post(
#     req: Optional[TransferkuRelationRequest] = Body(default=None),
# ):
#     try:
#         tx_type = req.transaction_type if req else None
#         rel_type = req.relation_type if req else None
#         data = get_transferku_relation(transaction_type=tx_type, relation_type=rel_type)
#         return BaseResponse(
#             status="success",
#             message="Data fetched successfully",
#             data=data,
#         )
#     except Exception as e:
#         print(f"[ERROR get_transferku_relation] {type(e).__name__}: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

@router.get("/get_transferku_relation", response_model=BaseResponse[list[dict[str, str]]])
@router.get("/get_transferku_relations", response_model=BaseResponse[list[dict[str, str]]])
async def get_transferku_relation_get(
    transaction_type: Optional[str] = None,
    relation_type: Optional[str] = None,
):
    try:
        data = get_transferku_relation(transaction_type=transaction_type, relation_type=relation_type)
        return BaseResponse(
            status="success",
            message="Data fetched successfully",
            data=data,
        )
    except Exception as e:
        print(f"[ERROR get_transferku_relation] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# @router.post("/get_transferku_business_relation", response_model=BaseResponse[list[dict[str, str]]])
# @router.get("/get_transferku_business_relation", response_model=BaseResponse[list[dict[str, str]]])
# async def get_transferku_business_relation_endpoint():
#     try:
#         data = get_transferku_business_relation()
#         return BaseResponse(
#             status="success",
#             message="Data fetched successfully",
#             data=data,
#         )
#     except Exception as e:
#         print(f"[ERROR get_transferku_business_relation] {type(e).__name__}: {e}")
#         raise HTTPException(status_code=500, detail=str(e))

@router.post("/get_transferku_source_of_fund", response_model=BaseResponse[list[dict[str, str]]])
@router.post("/get_transferku_source_of_funds", response_model=BaseResponse[list[dict[str, str]]])
async def get_transferku_source_of_fund_post(
    req: Optional[TransferkuSourceOfFundRequest] = Body(default=None),
):
    try:
        data = get_transferku_source_of_fund()
        return BaseResponse(
            status="success",
            message="Data fetched successfully",
            data=data,
        )
    except Exception as e:
        print(f"[ERROR get_transferku_source_of_fund] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/get_transferku_source_of_fund", response_model=BaseResponse[list[dict[str, str]]])
@router.get("/get_transferku_source_of_funds", response_model=BaseResponse[list[dict[str, str]]])
async def get_transferku_source_of_fund_get():
    try:
        data = get_transferku_source_of_fund()
        return BaseResponse(
            status="success",
            message="Data fetched successfully",
            data=data,
        )
    except Exception as e:
        print(f"[ERROR get_transferku_source_of_fund] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

