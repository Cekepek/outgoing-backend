import asyncio
from typing import Any
from fastapi import APIRouter, HTTPException
import httpx
from sqlalchemy import null

from app.schemas import BankItem, BankRequest, BaseResponse, CatalogueItem, CatalogueRequest, ErrorItems, ExchangeRateItem, RateItem, RateItemSuccess, RateRequest, ResponseSchema
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


async def fetch_bank_list(payout_country: str) -> list[dict]:
    url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetAgentList"
    signature, body = build_request("POST", url, {
        "agentSessionId": "",
        "paymentMode": "B",
        "payoutCountry": payout_country,
    })
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=body, headers={"Authorization": signature})
        response.raise_for_status()
        payload = response.json()

    if payload.get("code") != "0":
        raise HTTPException(status_code=502, detail=f"GetAgentList failed: {payload.get('message', '')}")

    return payload.get("locationDetail") or []


async def fetch_rate(
    transfer_amount: float,
    calc_by: str,
    payout_currency: str,
    payment_mode: str,
    location_id: str,
    payout_country: str,
) -> dict | None:
    url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetEXRate"
    signature, body = build_request("POST", url, {
        "agentSessionId": "",
        "transferAmount": transfer_amount,
        "calcBy": calc_by,
        "payoutCurrency": payout_currency,
        "paymentMode": payment_mode,
        "locationId": location_id,
        "payoutCountry": payout_country,
    })
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, json=body, headers={"Authorization": signature})
        response.raise_for_status()
        payload = response.json()

    if payload.get("code") != "0":
        print(f"[WARN fetch_rate] locationId={location_id} failed: {payload.get('message', '')}")
        return None

    return payload


async def best_bank_for_country(
    payout_country: str,
    payout_currency: str,
    transfer_amount: float,
    calc_by: str,
    payment_mode: str,
) -> dict | None:
    banks = await fetch_bank_list(payout_country)

    # exclude aggregate "ALL BANKS" style entries — not a real payout bank
    real_banks = [b for b in banks if b["locationId"] != f"{payout_country[:3].upper()}ALL"]
    if not real_banks:
        return None

    rate_results = await asyncio.gather(*[
        fetch_rate(
            transfer_amount=transfer_amount,
            calc_by=calc_by,
            payout_currency=payout_currency,
            payment_mode=payment_mode,
            location_id=bank["locationId"],
            payout_country=payout_country,
        )
        for bank in real_banks
    ], return_exceptions=True)

    candidates = []
    for bank, result in zip(real_banks, rate_results):
        if isinstance(result, Exception) or result is None:
            continue
        candidates.append({"bank": bank, "rate": result})

    if not candidates:
        return None

    # payoutAmount is already net of serviceCharge/vatCharge — best single metric
    best = max(candidates, key=lambda c: float(c["rate"]["payoutAmount"]))
    return best

@router.post("/get_rate")
async def get_rate(rate_request: RateRequest):
    try:
        result = await best_bank_for_country(
            rate_request.payout_country,
            rate_request.payout_currency,
            rate_request.transfer_amount,
            rate_request.calc_by,
            rate_request.payment_mode,
        )
        if(result !=None):
            return BaseResponse(status="success", message="Data fetched successfully", data=result)
        else:
            return BaseResponse(status="error", message="Data not found", data=[])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))