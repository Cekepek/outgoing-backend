import asyncio
from datetime import datetime
import random
import string
from fastapi import HTTPException
import httpx
from app.utils.signature import build_request
from app.config import settings


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
    transfer_amount: str,
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
    transfer_amount: str,
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
    
#TRANSFERKU
PAYERS_NOT_FOUND_STATUS = "1000997"


def generate_transferku_external_id() -> str:
    """Generates a unique 24-digit external ID (timestamp + random digits)."""
    return datetime.now().strftime("%Y%m%d%H%M%S") + "".join(random.choices(string.digits, k=10))


async def fetch_countries(client: httpx.AsyncClient) -> list[dict]:
    try:
        resp = await client.get(
            f"{settings.payment_host_transferku}/v1/countries",
            auth=httpx.BasicAuth(settings.payment_client_id_transferku, settings.payment_client_secret_transferku)
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"[ERROR fetch_countries] {e}")
        return []


async def fetch_payers_for_country(
    client: httpx.AsyncClient, iso_code: str
) -> list[dict] | None:
    """
    Returns the list of payers for a country, or None if the API
    responded with the "not found" status.
    """
    try: 
        resp = await client.post(
            f"{settings.payment_host_transferku}/v1/payers",
            auth=httpx.BasicAuth(settings.payment_client_id_transferku, settings.payment_client_secret_transferku),
            json={"iso_code": iso_code},
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        print(f"[ERROR] {iso_code}: {e.response.status_code} - {e.response.text}")
        return None
    except Exception as e:
        print(f"[ERROR] {iso_code}: {type(e).__name__}: {e}")
        return None
    # "not found" comes back as a dict with a status/status_message,
    # not a list of payers
    if isinstance(data, dict) and data.get("status") == PAYERS_NOT_FOUND_STATUS:
        return None

    # Defensive: some APIs might wrap the list, e.g. {"data": [...]}
    if isinstance(data, dict) and "data" in data:
        data = data["data"]

    return data


def payer_has_valid_transaction_type(payer: dict) -> bool:
    """
    A payer counts if ANY of its transaction_types has
    maximum_transaction_amount > 0.
    """
    transaction_types = payer.get("transaction_types", {})
    for tx_config in transaction_types.values():
        if tx_config.get("maximum_transaction_amount", 0) > 0:
            return True
    return False


async def get_countries_with_valid_payers() -> list[dict]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        countries = await fetch_countries(client)

        results: list[dict] = []

        # concurrency limit so we don't hammer the payers endpoint
        semaphore = asyncio.Semaphore(10)

        async def check_country(country: dict):
            iso_code = country["iso_code"]
            async with semaphore:
                try:
                    payers = await fetch_payers_for_country(client, iso_code)
                except httpx.HTTPStatusError:
                    # treat hard errors as "no payers" rather than crashing
                    # the whole batch; log this in real usage
                    return

            if not payers:
                return

            valid_payers = [p for p in payers if payer_has_valid_transaction_type(p)]

            if valid_payers:
                results.append(
                    {
                        "iso_code": iso_code,
                        "name": country["name"],
                        "payer_ids": [p["payer_id"] for p in valid_payers],
                        "payers": valid_payers,
                    }
                )

        await asyncio.gather(*(check_country(c) for c in countries))

        order = {c["iso_code"]: i for i, c in enumerate(countries)}
        results.sort(key=lambda r: order[r["iso_code"]])

        return results


async def fetch_quote_transferku(
    client: httpx.AsyncClient,
    payer_id: str,
    payout_country: str,
    payout_currency: str,
    amount: float | int,
    mode: str = "DESTINATION_AMOUNT",
) -> dict | None:
    url = f"{settings.payment_host_transferku}/v1/quotes"
    formatted_amount = int(amount) if isinstance(amount, (int, float)) and float(amount).is_integer() else amount
    body = {
        "external_id": generate_transferku_external_id(),
        "mode": mode,
        "type": "C2C",
        "payer_id": str(payer_id),
        "source": {
            "country_iso_code": "IDN",
            "currency": "IDR"
        },
        "destination": {
            "country_iso_code": payout_country,
            "currency": payout_currency,
            "amount": formatted_amount
        }
    }
    try:
        resp = await client.post(
            url,
            auth=httpx.BasicAuth(settings.payment_client_id_transferku, settings.payment_client_secret_transferku),
            json=body,
            timeout=15.0
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        print(f"[ERROR fetch_quote_transferku] payer_id={payer_id} HTTP {e.response.status_code}: {e.response.text}")
        return None
    except Exception as e:
        print(f"[ERROR fetch_quote_transferku] payer_id={payer_id} {type(e).__name__}: {e}")
        return None


def _extract_transferku_amounts(quote: dict) -> tuple[float, float]:
    """Extracts (source_amount, destination_amount) from a Transferku quote response."""
    data = quote.get("data", quote) if isinstance(quote.get("data"), dict) else quote

    # Source amount (IDR collect amount)
    source_amt = 0.0
    if isinstance(data.get("source"), dict) and "amount" in data["source"]:
        try:
            source_amt = float(data["source"]["amount"])
        except (ValueError, TypeError):
            pass
    elif "source_amount" in data:
        try:
            source_amt = float(data["source_amount"])
        except (ValueError, TypeError):
            pass
    elif "collect_amount" in data:
        try:
            source_amt = float(data["collect_amount"])
        except (ValueError, TypeError):
            pass
    elif "total_amount" in data:
        try:
            source_amt = float(data["total_amount"])
        except (ValueError, TypeError):
            pass

    # Destination amount (Payout amount)
    dest_amt = 0.0
    if isinstance(data.get("destination"), dict) and "amount" in data["destination"]:
        try:
            dest_amt = float(data["destination"]["amount"])
        except (ValueError, TypeError):
            pass
    elif "destination_amount" in data:
        try:
            dest_amt = float(data["destination_amount"])
        except (ValueError, TypeError):
            pass
    elif "payout_amount" in data:
        try:
            dest_amt = float(data["payout_amount"])
        except (ValueError, TypeError):
            pass

    return source_amt, dest_amt


async def compare_transferku_and_lightremit(
    payout_country: str,
    payout_currency: str,
    transfer_amount: float | str,
    calc_by: str = "P",
    payment_mode: str = "B",
) -> dict | None:
    amount_float = transfer_amount

    # 1. Concurrently fetch LightRemit best bank rate and Transferku valid countries
    lightremit_task = best_bank_for_country(
        payout_country=payout_country,
        payout_currency=payout_currency,
        transfer_amount=amount_float,
        calc_by=calc_by,
        payment_mode=payment_mode,
    )
    valid_countries_task = get_countries_with_valid_payers()

    lightremit_result, valid_countries = await asyncio.gather(
        lightremit_task,
        valid_countries_task,
        return_exceptions=True
    )

    if isinstance(lightremit_result, Exception):
        print(f"[WARN compare_transferku_and_lightremit] LightRemit failed: {lightremit_result}")
        lightremit_result = None

    if isinstance(valid_countries, Exception):
        print(f"[WARN compare_transferku_and_lightremit] get_countries_with_valid_payers failed: {valid_countries}")
        valid_countries = []

    # 2. Check if requested payout_country is in valid Transferku countries
    matching_country = next(
        (c for c in (valid_countries or []) if c.get("iso_code", "").upper() == payout_country.upper()),
        None
    )

    # If country is not supported by Transferku, automatically choose LightRemit
    if not matching_country or not matching_country.get("payer_ids"):
        if lightremit_result is not None:
            return {
                "chosen_agent": "LIGHTREMIT",
                "bank": lightremit_result.get("bank"),
                "rate": lightremit_result.get("rate"),
            }
        return None

    # 3. Country is supported by Transferku -> fetch quote(s) from /v1/quotes
    payer_ids = matching_country.get("payer_ids", [])
    transferku_quote = None

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Request quote for available valid payer(s)
        quote_tasks = [
            fetch_quote_transferku(
                client=client,
                payer_id=pid,
                payout_country=payout_country,
                payout_currency=payout_currency,
                amount=amount_float,
                mode="DESTINATION_AMOUNT",
            )
            for pid in payer_ids
        ]
        quote_results = await asyncio.gather(*quote_tasks, return_exceptions=True)

        valid_quotes = [q for q in quote_results if isinstance(q, dict) and q]
        if valid_quotes:
            # Pick best Transferku quote (lowest source amount if destination amount mode)
            transferku_quote = min(
                valid_quotes,
                key=lambda q: _extract_transferku_amounts(q)[0] or float("inf")
            )

    # 4. Compare results
    if transferku_quote is None and lightremit_result is None:
        return None

    if transferku_quote is None:
        return {
            "chosen_agent": "LIGHTREMIT",
            "bank": lightremit_result.get("bank"),
            "rate": lightremit_result.get("rate"),
        }

    if lightremit_result is None:
        return {
            "chosen_agent": "TRANSFERKU",
            "quote": transferku_quote,
        }

    # Both succeeded -> compare final amounts
    lr_rate = lightremit_result.get("rate", {})
    try:
        lr_collect = float(lr_rate.get("collectAmount", 0))
    except (ValueError, TypeError):
        lr_collect = 0.0

    try:
        lr_payout = float(lr_rate.get("payoutAmount", 0))
    except (ValueError, TypeError):
        lr_payout = 0.0

    tk_source, tk_dest = _extract_transferku_amounts(transferku_quote)

    # If calc_by == "C" (Source amount fixed): choose whichever gives higher destination payout
    if calc_by == "C":
        if tk_dest > lr_payout:
            chosen_agent = "TRANSFERKU"
        else:
            chosen_agent = "LIGHTREMIT"
    else:
        # Default: DESTINATION_AMOUNT / calc_by == "P" (Destination amount fixed):
        # choose whichever requires lower source collect amount (in IDR)
        if tk_source > 0 and lr_collect > 0:
            if tk_source < lr_collect:
                chosen_agent = "TRANSFERKU"
            else:
                chosen_agent = "LIGHTREMIT"
        elif tk_source > 0:
            chosen_agent = "TRANSFERKU"
        else:
            chosen_agent = "LIGHTREMIT"

    if chosen_agent == "TRANSFERKU":
        return {
            "chosen_agent": "TRANSFERKU",
            "quote": transferku_quote,
        }
    else:
        return {
            "chosen_agent": "LIGHTREMIT",
            "bank": lightremit_result.get("bank"),
            "rate": lightremit_result.get("rate"),
        }