import asyncio
from datetime import datetime, timezone
import difflib
import json
import logging
from pathlib import Path
import random
import re
import string
from typing import Any

from fastapi import HTTPException
import httpx
from redis.exceptions import RedisError

from app.config import settings
from app.utils.redis import get_redis_client, redis_client
from app.utils.signature import build_request

logger = logging.getLogger(__name__)


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

MAJOR_BANKS: dict[str, list[dict[str, Any]]] = {
    "HKG": [
        {"name": "Bank of China (Hong Kong)", "swift": "BKCHHKHH",
         "aliases": ["BANKOFCHINAHONGKONG", "BANKOFCHINA"]},
        {"name": "Citibank", "swift": "CITIHKAX",
         "aliases": ["CITIBANK"]},
        {"name": "Hang Seng Bank", "swift": "HASEHKHH",
         "aliases": ["HANGSENG"]},
        {"name": "HSBC", "swift": "HSBCHKHH",
         "aliases": ["HSBC", "HONGKONGANDSHANGHAIBANKING"]},
        {"name": "Standard Chartered Bank", "swift": "SCBLHKHH",
         "aliases": ["STANDARDCHARTERED"]},
        {"name": "DBS Bank (Hong Kong)", "swift": "DHBKHKHH",
         "aliases": ["DBSBANK", "DBSHONGKONG"]},
        {"name": "Bank of East Asia", "swift": "BEASHKHH",
         "aliases": ["BANKOFEASTASIA"]},
        {"name": "China Construction Bank (Asia)", "swift": "PCBCHKHH",
         "aliases": ["CHINACONSTRUCTIONBANK"]},
        {"name": "ICBC (Asia)", "swift": "UBHKHKHH",
         "aliases": ["ICBCASIA", "INDUSTRIALANDCOMMERCIALBANKOFCHINA"]},
        {"name": "Bank of Communications (Hong Kong)", "swift": "COMMHKHH",
         "aliases": ["BANKOFCOMMUNICATIONS"]},
        {"name": "OCBC Bank (Hong Kong)", "swift": "WIHBHKHH",
         "aliases": ["OCBC", "OVERSEACHINESEBANKING"]},
        {"name": "Dah Sing Bank", "swift": "DSBAHKHH",
         "aliases": ["DAHSING"]},
        {"name": "CMB Wing Lung Bank", "swift": "WLBKHKHH",
         "aliases": ["WINGLUNG"]},
        {"name": "Chong Hing Bank", "swift": "LCHBHKHH",
         "aliases": ["CHONGHING"]},
        {"name": "Nanyang Commercial Bank", "swift": "NYCBHKHH",
         "aliases": ["NANYANGCOMMERCIAL"]},
        {"name": "China CITIC Bank International", "swift": "CIBKHKHH",
         "aliases": ["CHINACITIC", "CITICBANKINTERNATIONAL"]},
    ],
    "SGP": [
        {"name": "Citibank", "swift": "CITISGSGGCB",
         "aliases": ["CITIBANK"]},
        {"name": "DBS Bank", "swift": "DBSSSGSG",
         "aliases": ["DBSBANK", "DBSINGAPORE"]},
        {"name": "HSBC BANK (SINGAPORE) LIMITED", "swift": "HSBCSGS2",
         "aliases": ["HSBCBANK", "HSBCBANK(SINGAPORE)LIMITED"]},
        {"name": "MAYBANK SINGAPORE LIMITED", "swift": "MBBESGSG",
         "aliases": ["MAYBANK", "MAYBANK(SINGAPORE)LIMITED"]},
        {"name": "OCBC BANK (SINGAPORE) LIMITED", "swift": "OCBDSGSG",
         "aliases": ["OCBC", "OCBCBANK(SINGAPORE)LIMITED"]},
        {"name": "Standard Chartered Bank", "swift": "SCBLHKHH",
         "aliases": ["STANDARDCHARTERED"]},
        {"name": "UOB Limited", "swift": "UOVBSGSG",
         "aliases": ["UOB"]},
    ],
    # "IDN": [...], "SGP": [...]  add other countries here
}

def enrich_catalogue(catalogue_type: str, raw_result: list[dict]) -> list[dict]:
    if catalogue_type == "CTY":
        return [
            {
                "value": item["data"],
                "description": COUNTRY_NAMES.get(item["value"], item["value"]),
                "optionalField": CURRENCY_NAME.get(item["value"], item["value"]),
            }
            for item in raw_result
        ]
        return [
            {
                "value": item["data"],
                "description": item["value"],
                "optionalField": item.get("optionalField", ""),
            }
            for item in raw_result
        ]

async def fetch_bank_list(payout_country: str, payment_mode: str = "B") -> list[dict]:
    try:
        url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/GetAgentList"
        signature, body = build_request("POST", url, {
            "agentSessionId": "",
            "paymentMode": payment_mode,
            "payoutCountry": payout_country,
        })
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=body, headers={"Authorization": signature})
            response.raise_for_status()
            payload = response.json()
        
        if payload.get("code") != "0":
            raise HTTPException(status_code=502, detail=f"GetAgentList failed: {payload.get('message', '')}")
        
        raw_locations = payload.get("locationDetail") or []
        return [
            {
                "value": loc.get("locationId") or loc.get("value"),
                "description": loc.get("locationName") or loc.get("description"),
                "optionalField": loc.get("optionalField", ""),
            }
            for loc in raw_locations
        ]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"GetAgentList failed: {str(e)}")

async def fetch_rate(
    transfer_amount: str,
    calc_by: str,
    payout_currency: str,
    payment_mode: str,
    location_id: str,
    payout_country: str,
) -> tuple[dict | None, str | None]:
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
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=body, headers={"Authorization": signature})
            response.raise_for_status()
            payload = response.json()

        if payload.get("code") != "0":
            err_msg = payload.get("message", "")
            print(f"[WARN fetch_rate] locationId={location_id} failed: {err_msg}")
            return None, err_msg

        return payload, None
    except Exception as e:
        print(f"[WARN fetch_rate] locationId={location_id} exception: {e}")
        return None, str(e)

#TRANSFERKU
PAYERS_NOT_FOUND_STATUS = "1000997"


def generate_transferku_external_id() -> str:
    """Generates a unique 24-digit external ID (timestamp + random digits)."""
    return datetime.now().strftime("%Y%m%d%H%M%S") + "".join(random.choices(string.digits, k=10))


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


def payer_matches_country_currency(payer: dict, iso_code: str | None) -> bool:
    """
    A payer is kept only if its settlement currency is the country's own currency.
    Drops cross-currency payers such as HKG + GBP ("All Banks | SWIFT").
    """
    expected = CURRENCY_NAME.get((iso_code or "").strip().upper())
    if not expected:
        return True  # no rule for this country, don't filter

    payer_currency = str(payer.get("currency") or "").strip().upper()
    if not payer_currency:
        return True  # nothing to compare against

    return payer_currency == expected


def payer_has_valid_transaction_type(payer: dict, iso_code: str | None = None) -> bool:
    """
    A payer counts if:
      - (when iso_code is given) its currency matches the country's currency, and
      - ANY of its transaction_types has maximum_transaction_amount > 0.
    """
    if iso_code is not None and not payer_matches_country_currency(payer, iso_code):
        logger.debug(
            "dropping payer %s (%s): currency %s != expected for %s",
            payer.get("payer_id"), payer.get("name"), payer.get("currency"), iso_code,
        )
        return False

    transaction_types = payer.get("transaction_types", {})
    for tx_config in transaction_types.values():
        if tx_config.get("maximum_transaction_amount", 0) > 0:
            return True
    return False


async def fetch_quote_transferku(
    client: httpx.AsyncClient,
    payer_id: str,
    payout_country: str,
    payout_currency: str,
    amount: float | int | str,
    mode: str = "DESTINATION_AMOUNT",
    transaction_type: str = "C2C",
) -> tuple[dict | None, str | None]:
    url = f"{settings.payment_host_transferku}/v1/quotes"
    try:
        amt_num = float(amount) if isinstance(amount, (str, int, float)) else 0.0
        formatted_amount = int(amt_num) if amt_num.is_integer() else amt_num
    except (ValueError, TypeError):
        formatted_amount = amount

    body = {
        "external_id": generate_transferku_external_id(),
        "mode": mode,
        "type": transaction_type or "C2C",
        "payer_id": str(payer_id),
        "source": {
            "country_iso_code": "IDN",
            "currency": "IDR"
        },
        "destination": {
            "country_iso_code": payout_country,
            "currency": payout_currency,
        }
    }
    if mode == "SOURCE_AMOUNT":
        body["source"]["amount"] = formatted_amount
    else:
        body["destination"]["amount"] = formatted_amount

    try:
        resp = await client.post(
            url,
            auth=httpx.BasicAuth(settings.payment_client_id_transferku, settings.payment_client_secret_transferku),
            json=body,
            timeout=15.0
        )
        resp.raise_for_status()
        return resp.json(), None
    except httpx.HTTPStatusError as e:
        err_text = e.response.text
        print(f"[ERROR fetch_quote_transferku] payer_id={payer_id} HTTP {e.response.status_code}: {err_text}")
        try:
            err_json = e.response.json()
            err_msg = err_json.get("message") or err_json.get("status_message") or err_text
        except Exception:
            err_msg = err_text
        return None, err_msg
    except Exception as e:
        print(f"[ERROR fetch_quote_transferku] payer_id={payer_id} {type(e).__name__}: {e}")
        return None, str(e)


def normalize_location_name(name: str | None) -> str:
    """Normalizes location/bank name for accurate matching."""
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s]", " ", s)
    return " ".join(s.split())


def calculate_location_similarity(name1: str | None, name2: str | None) -> float:
    """
    Calculates similarity score (0.0 to 1.0) between two location or bank names.
    Handles variations like 'BANK OF CHINA', 'Bank of China Limited', etc.
    """
    n1 = normalize_location_name(name1)
    n2 = normalize_location_name(name2)
    if not n1 or not n2:
        return 0.0
    if n1 == n2:
        return 1.0

    tokens1 = set(n1.split())
    tokens2 = set(n2.split())
    noise_words = {
        "bank", "ltd", "limited", "co", "the", "pt", "tbk", "inc", "corp",
        "corporation", "berhad", "bhd", "of", "and", "national", "international"
    }
    filt1 = {t for t in tokens1 if t not in noise_words} or tokens1
    filt2 = {t for t in tokens2 if t not in noise_words} or tokens2

    token_sim = 0.0
    if filt1 and filt2:
        intersection = filt1.intersection(filt2)
        union = filt1.union(filt2)
        token_sim = len(intersection) / len(union)

    seq_sim = difflib.SequenceMatcher(None, n1, n2).ratio()
    score = (token_sim * 0.6) + (seq_sim * 0.4)

    if filt1 == filt2:
        score = max(score, 0.95)
    elif filt1.issubset(filt2) or filt2.issubset(filt1):
        subset_ratio = min(len(filt1), len(filt2)) / max(len(filt1), len(filt2))
        score = max(score, 0.70 + 0.25 * subset_ratio)

    return min(score, 1.0)


def _format_error_message(lr_err: str | None = None, tk_err: str | None = None) -> str:
    if lr_err and "service charge is not defined" in lr_err.lower():
        return "the amount is too high or too low"
    errs = [str(e).strip() for e in [lr_err, tk_err] if e and str(e).strip()]
    return " | ".join(errs) if errs else "Rate not available"

async def get_direct_rate(
    payout_country: str,
    payout_currency: str,
    transfer_amount: str | float | int,
    canonical_id: str,
    calc_by: str = "P",
    payment_mode: str = "B",
    transaction_type: str = "C2C",
) -> tuple[dict | None, str | None]:
    country_iso = payout_country.strip().upper()
    str_amount = str(transfer_amount)

    def _to_float(val, default=0.0):
        if val is None or val == "":
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # 1. Resolve provider data from cache (server-controlled, not client input)
    loc = await get_bank(country_iso, canonical_id.strip(), payment_mode, transaction_type)
    if loc is None:
        return None, "Unknown location"

    location_name = loc.get("name", "")
    providers = loc.get("providers") or {}
    tk_cfg = providers.get("transferku")
    lr_cfg = providers.get("lightremit")

    # 2. Fetch only from providers that support this location
    async def _fetch_tk():
        if not tk_cfg or not tk_cfg.get("payer_id"):
            return None, "Transferku not available for this location"
        mode = "SOURCE_AMOUNT" if calc_by == "C" else "DESTINATION_AMOUNT"
        try:
            amount_val = float(transfer_amount)
        except (ValueError, TypeError):
            amount_val = transfer_amount
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                return await fetch_quote_transferku(
                    client=client,
                    payer_id=tk_cfg["payer_id"],
                    payout_country=country_iso,
                    payout_currency=payout_currency,
                    amount=amount_val,
                    mode=mode,
                    transaction_type=tk_cfg.get("transaction_type") or transaction_type,
                )
        except Exception as e:
            return None, str(e)

    async def _fetch_lr():
        if not lr_cfg or not lr_cfg.get("location_id"):
            return None, "LightRemit not available for this location"
        try:
            return await fetch_rate(
                transfer_amount=str_amount,
                calc_by=calc_by,
                payout_currency=payout_currency,
                payment_mode=payment_mode,
                location_id=lr_cfg["location_id"],
                payout_country=country_iso,
            )
        except Exception as e:
            return None, str(e)

    (tk_quote, tk_err), (lr_rate, lr_err) = await asyncio.gather(_fetch_tk(), _fetch_lr())

    if tk_quote is None and lr_rate is None:
        return None, _format_error_message(lr_err, tk_err)

    # 5. Extract amounts for comparison
    tk_source = 0.0
    tk_dest = 0.0
    tk_sent = 0.0
    tk_fee = 0.0
    tk_rate_val = 0.0

    if tk_quote:
        tk_source = _to_float(tk_quote.get("source", {}).get("amount"))
        tk_dest = _to_float(tk_quote.get("destination", {}).get("amount"))
        tk_sent = _to_float(tk_quote.get("sent_amount", {}).get("amount") or tk_source)
        tk_fee = _to_float(tk_quote.get("fee", {}).get("amount"))
        tk_rate_val = _to_float(tk_quote.get("inverse_fx_rate") or tk_quote.get("wholesale_fx_rate"))

    lr_exchange = 0.0
    lr_fee = 0.0
    lr_collect = 0.0
    lr_nominal = 0.0
    lr_payout = 0.0

    if lr_rate:
        lr_rate.pop("agentSessionId", None)
        lr_exchange = _to_float(lr_rate.get("exchangeRate"))
        lr_fee = _to_float(lr_rate.get("serviceCharge"))
        lr_collect = _to_float(lr_rate.get("collectAmount"))
        lr_nominal = _to_float(lr_rate.get("transferAmount"))
        lr_payout = _to_float(lr_rate.get("payoutAmount"))

    # 6. Compare and select provider
    if tk_quote is not None and lr_rate is not None:
        if calc_by == "C":
            # Fixed source amount: choose higher destination payout amount
            if tk_dest > lr_payout:
                chosen_agent = "TRANSFERKU"
            else:
                chosen_agent = "LIGHTREMIT"
        else:
            # Fixed destination amount (calc_by == "P"): choose lower total pay / collect amount
            if tk_sent > 0 and lr_collect > 0:
                if tk_sent < lr_collect:
                    chosen_agent = "TRANSFERKU"
                else:
                    chosen_agent = "LIGHTREMIT"
            elif tk_sent > 0:
                chosen_agent = "TRANSFERKU"
            else:
                chosen_agent = "LIGHTREMIT"
    elif tk_quote is not None:
        chosen_agent = "TRANSFERKU"
    else:
        chosen_agent = "LIGHTREMIT"

    # 7. Format winner response
    if chosen_agent == "TRANSFERKU":
        return {
            "chosen_agent": "TRANSFERKU",
            "provider": "TRANSFERKU",
            "canonical_id": canonical_id,
            "location_id": tk_cfg["location_id"],
            "location_name": location_name,
            "exchange_rate": tk_rate_val,
            "admin_fee": tk_fee,
            "total_pay": tk_sent,
            "nominal_dikirim": tk_source,
            "payout_amount": tk_dest,
            "payout_currency": tk_quote.get("destination", {}).get("currency") or payout_currency,
            "quote": tk_quote,
        }, None

    return {
        "chosen_agent": "LIGHTREMIT",
        "provider": "LIGHTREMIT",
        "canonical_id": canonical_id,
        "location_id": lr_cfg["location_id"],
        "location_name": location_name,
        "exchange_rate": lr_exchange,
        "admin_fee": lr_fee,
        "total_pay": lr_collect,
        "nominal_dikirim": lr_nominal,
        "payout_amount": lr_payout,
        "payout_currency": lr_rate.get("payoutCurrency") or payout_currency,
        "rate": lr_rate,
        "bank": {
            "locationId": lr_cfg["location_id"],
            "locationName": location_name,
            "message": lr_rate.get("message", ""),
        },
    }, None


get_rate = get_direct_rate


# ---------------------------------------------------------------------------
# relation_catalogues.json — loaded once at import time
# ---------------------------------------------------------------------------
_CATALOGUES_PATH = Path(__file__).parent.parent / "utils" / "relation_catalogues.json"
_RELATION_CATALOGUES: dict = {}
try:
    with open(_CATALOGUES_PATH, "r", encoding="utf-8") as _f:
        _RELATION_CATALOGUES = json.load(_f)
except Exception as _e:
    print(f"[WARN] Could not load relation_catalogues.json: {_e}")

# Build a fast lookup dict:  { section: { value: description } }
_PURPOSE_LOOKUP: dict[str, str] = {
    item["value"]: item["description"]
    for item in _RELATION_CATALOGUES.get("purpose_of_remittance", [])
}


def _purpose_label(value: str) -> str:
    """Return a human-readable label for a purpose-of-remittance code.

    Falls back to title-casing the raw value (e.g. 'NEW_CODE' → 'New Code')
    when the code is not present in relation_catalogues.json.
    """
    if value in _PURPOSE_LOOKUP:
        return _PURPOSE_LOOKUP[value]
    return value.replace("_", " ").title()


async def get_transferku_purpose_of_remittance(
    iso_code: str,
    payer_id: str,
    transaction_type: str | None = None,
    force_refresh: bool = False,
) -> tuple[list[dict[str, str]] | None, str | None]:
    # 1. Construct the Redis cache key using payer_id and transaction_type
    tx_key = transaction_type.strip().upper() if transaction_type else "ALL"
    cache_key = f"transferku:purposes:{payer_id}:{tx_key}"

    # 2. Check if cached purposes exist in Redis
    if not force_refresh:
        try:
            cached_data = await redis_client.get(cache_key)
            if cached_data:
                return json.loads(cached_data), None
        except Exception as e:
            print(f"[WARN Redis get] Failed to read purposes cache for '{cache_key}': {e}")

    # 3. Fetch from Transferku API if not cached
    async with httpx.AsyncClient(timeout=15.0) as client:
        payers = await fetch_payers_for_country(client, iso_code)

    if payers is None:
        return None, f"Payers not found for country '{iso_code}'"

    # Find the requested payer by payer_id
    matched_payer = next(
        (p for p in payers if str(p.get("payer_id")) == str(payer_id)),
        None,
    )
    if not matched_payer:
        return None, f"Payer '{payer_id}' not found for country '{iso_code}'"

    transaction_types = matched_payer.get("transaction_types", {})
    if not transaction_types:
        return None, f"No transaction types found for payer '{payer_id}'"

    def _to_items(raw_values: list[str]) -> list[dict[str, str]]:
        return [
            {
                "value": v,
                "label": _purpose_label(v),
                "description": _purpose_label(v),
            }
            for v in raw_values
        ]

    # If specific transaction_type is specified (e.g. C2C, C2B, B2C, B2B)
    if transaction_type:
        tx_info = transaction_types.get(transaction_type)
        if not tx_info:
            return None, f"Transaction type '{transaction_type}' not found for payer '{payer_id}'"
        result = _to_items(tx_info.get("purpose_of_remittance_values_accepted", []))
    else:
        # If transaction_type is not specified, collect all unique purpose values across transaction types
        raw_purposes: list[str] = []
        seen: set[str] = set()
        for _, tx_info in transaction_types.items():
            if isinstance(tx_info, dict):
                for val in tx_info.get("purpose_of_remittance_values_accepted", []):
                    if val not in seen:
                        seen.add(val)
                        raw_purposes.append(val)
        result = _to_items(raw_purposes)

    # 4. Save fetched purposes into Redis with TTL
    try:
        await redis_client.set(
            cache_key,
            json.dumps(result),
            ex=getattr(settings, "redis_cache_ttl", 86400),
        )
    except Exception as e:
        print(f"[WARN Redis set] Failed to cache purposes for '{cache_key}': {e}")

    return result, None



# Alias for backward compatibility
get_transferku_catalogue = get_transferku_purpose_of_remittance


def get_transferku_source_of_fund() -> list[dict[str, str]]:
    """Return Transferku source of fund catalogue mapped with value, label, and description."""
    raw_list = _RELATION_CATALOGUES.get("source_of_fund", [])
    return [
        {
            "value": item["value"],
            "label": item["description"],
            "description": item["description"],
        }
        for item in raw_list
        if isinstance(item, dict) and "value" in item and "description" in item
    ]


def get_transferku_relation(
    transaction_type: str | None = None,
    relation_type: str | None = None,
) -> list[dict[str, str]]:
    """Return Transferku relation catalogue mapped with value, label, and description.

    - If relation_type is 'all', returns combined personal and business relations.
    - If transaction_type is a business type (B2B, B2C, C2B, BUSINESS) or relation_type is 'business',
      returns business_relation.
    - Otherwise returns personal relation.
    """
    if relation_type and relation_type.lower() == "all":
        raw_list = _RELATION_CATALOGUES.get("relation", []) + _RELATION_CATALOGUES.get("business_relation", [])
        return [
            {
                "value": item["value"],
                "label": item["description"],
                "description": item["description"],
            }
            for item in raw_list
            if isinstance(item, dict) and "value" in item and "description" in item
        ]

    is_business = False
    if transaction_type and transaction_type.upper() in ("B2B", "B2C", "C2B", "BUSINESS"):
        is_business = True
    elif relation_type and relation_type.lower() in ("business", "business_relation"):
        is_business = True

    key = "business_relation" if is_business else "relation"
    raw_list = _RELATION_CATALOGUES.get(key, [])
    return [
        {
            "value": item["value"],
            "label": item["description"],
            "description": item["description"],
        }
        for item in raw_list
        if isinstance(item, dict) and "value" in item and "description" in item
    ]


def get_transferku_business_relation() -> list[dict[str, str]]:
    """Return Transferku business relation catalogue mapped with value, label, and description."""
    raw_list = _RELATION_CATALOGUES.get("business_relation", [])
    return [
        {
            "value": item["value"],
            "label": item["description"],
            "description": item["description"],
        }
        for item in raw_list
        if isinstance(item, dict) and "value" in item and "description" in item
    ]


def extract_transferku_locations(
    payers: list[dict],
    transaction_type: str | None = None,
) -> list[dict]:
    """
    Extracts location details from Transferku payers.
    If transaction_type is provided (e.g. 'C2C', 'C2B', 'B2C', 'B2B'),
    extracts location_detail for that specific transaction type.
    Otherwise, extracts and deduplicates location_detail across all transaction types.
    Populates optionalField with payer_id.
    """
    locations: list[dict] = []
    seen_ids: set[str] = set()

    for payer in payers:
        payer_id = str(payer.get("payer_id") or "")
        tx_types = payer.get("transaction_types", {})
        if not isinstance(tx_types, dict):
            continue

        if transaction_type:
            types_to_check = {transaction_type: tx_types.get(transaction_type)} if transaction_type in tx_types else {}
        else:
            types_to_check = tx_types

        if not types_to_check:
            continue

        has_locations = False
        for _, tx_val in types_to_check.items():
            if not isinstance(tx_val, dict):
                continue
            for loc in tx_val.get("location_detail", []):
                has_locations = True
                loc_id = loc.get("locationId")
                loc_name = loc.get("locationName") or payer.get("name", "")
                if loc_id and loc_id not in seen_ids:
                    seen_ids.add(loc_id)
                    locations.append({
                        "value": loc.get("locationId"),
                        "description": loc_name,
                        "optionalField": payer_id or str(loc.get("optionalField", "")),
                    })
                elif not loc_id and loc_name:
                    key = (payer_id, loc_name)
                    if key not in seen_ids:
                        seen_ids.add(key)
                        locations.append({
                            "value": loc.get("value") or payer_id,
                            "description": loc_name,
                            "optionalField": payer_id or str(loc.get("optionalField", "")),
                        })

        if not has_locations and payer.get("name"):
            payer_name = str(payer.get("name") or "").strip()
            if payer_name.lower() not in {"bank transfer", "bank account", "wire transfer", "all banks"}:
                pid = payer_id or payer_name
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    locations.append({
                        "value": payer_id,
                        "description": payer_name,
                        "optionalField": payer_id,
                    })

    return locations

def merge_locations(lr: list[dict], tk: list[dict], mapping: dict[str, str]) -> list[dict]:
    """mapping: transferku location_id -> lightremit location_id (curated)."""
    merged: dict[str, dict] = {}
    for loc in lr:
        merged[loc["value"]] = {
            "canonical_id": loc["value"],
            "name": loc["description"],
            "providers": {"lightremit": {"location_id": loc["value"]}},
        }
    for loc in tk:
        key = mapping.get(loc["value"]) or loc["value"]   # unmapped -> its own entry
        entry = merged.setdefault(key, {
            "canonical_id": key,
            "name": loc["description"],
            "providers": {},
        })
        entry["providers"]["transferku"] = {
            "location_id": loc["value"],
            "payer_id": loc["optionalField"],
            "transaction_type": "C2C",
        }
    return list(merged.values())

def build_mapping_candidates(lr: list[dict], tk: list[dict]) -> list[dict]:
    lr_by_id = {l["value"]: l for l in lr}
    lr_by_name = {normalize_location_name(l["description"]): l for l in lr}
    rows = []
    for t in tk:
        name = normalize_location_name(t["description"])
        if t["value"] in lr_by_id and normalize_location_name(lr_by_id[t["value"]]["description"]) == name:
            rows.append({"tk": t["value"], "lr": t["value"], "status": "approved"})
        elif name in lr_by_name:
            rows.append({"tk": t["value"], "lr": lr_by_name[name]["value"], "status": "approved"})
        else:
            best = max(lr, key=lambda l: calculate_location_similarity(l["description"], t["description"]), default=None)
            if best:
                score = calculate_location_similarity(best["description"], t["description"])
                if score >= 0.8:
                    rows.append({"tk": t["value"], "lr": best["value"], "status": "pending_review", "score": score})
    return rows


def is_all_banks_location(loc: dict) -> bool:
    name = (loc.get("name") or "").lower()
    canonical = (loc.get("canonical_id") or "").upper()
    return "all banks" in name or "all bank" in name or canonical.endswith("ALL")


def matches_major_bank(loc: dict, major: dict) -> bool:
    loc_name = loc.get("name") or ""
    major_name = major.get("name") or ""
    if not loc_name or not major_name:
        return False

    norm_loc = normalize_location_name(loc_name)
    norm_major = normalize_location_name(major_name)
    if norm_loc and norm_loc == norm_major:
        return True

    clean_loc = re.sub(r"[^A-Z0-9]", "", loc_name.upper())
    clean_major = re.sub(r"[^A-Z0-9]", "", major_name.upper())
    if clean_loc and clean_loc == clean_major:
        return True

    clean_cid = re.sub(r"[^A-Z0-9]", "", (loc.get("canonical_id") or "").upper())
    for alias in major.get("aliases", []):
        clean_alias = re.sub(r"[^A-Z0-9]", "", alias.upper())
        if not clean_alias:
            continue
        if clean_alias == clean_loc:
            return True
        if len(clean_alias) >= 4 and clean_alias in clean_loc:
            return True
        if len(clean_loc) >= 4 and clean_loc in clean_alias:
            return True
        if len(clean_alias) >= 3 and clean_alias in clean_cid:
            return True
        if len(clean_cid) >= 4 and clean_cid in clean_alias:
            return True

    if loc.get("swift") and major.get("swift") and loc.get("swift").upper() == major.get("swift").upper():
        return True

    score = calculate_location_similarity(loc_name, major_name)
    if score >= 0.75:
        return True

    return False


def enrich_locations_with_major_banks(
    locations: list[dict],
    iso_code: str,
    keep_all_banks: bool = True,
) -> list[dict]:
    """
    Expands generic 'All Banks' entries into major banks with SWIFT codes prefilled.
    - If a major bank collides with an existing bank in the response, preserves its name,
      prefills SWIFT code, and attaches Transferku provider config.
    - If a major bank does not exist, a new entry is created with canonical_id '{iso}_{swift}'.
    - Generic 'All Banks' entries (e.g. SWIFT, LOCAL, PROXY) are kept at the bottom so
      unlisted banks and local clearing routes remain accessible.
    """
    country_iso = iso_code.strip().upper()
    major_list = MAJOR_BANKS.get(country_iso)
    if not major_list:
        return locations

    all_banks_entries = [loc for loc in locations if is_all_banks_location(loc)]
    if not all_banks_entries:
        return locations

    # Extract provider templates from "All Banks" entries for both LightRemit and Transferku
    all_banks_provider_template: dict[str, Any] = {}

    # 1. LightRemit template: find any all-banks entry with a lightremit provider
    for loc in all_banks_entries:
        lr_cfg = loc.get("providers", {}).get("lightremit")
        if lr_cfg:
            all_banks_provider_template["lightremit"] = dict(lr_cfg)
            break

    # 2. Transferku template: find all-banks entry with transferku (preferring "SWIFT" in name)
    tk_swift_entry = next(
        (
            loc
            for loc in all_banks_entries
            if "swift" in (loc.get("name") or "").lower()
            and loc.get("providers", {}).get("transferku")
        ),
        None,
    )
    if tk_swift_entry:
        all_banks_provider_template["transferku"] = dict(
            tk_swift_entry["providers"]["transferku"]
        )
    else:
        for loc in all_banks_entries:
            tk_cfg = loc.get("providers", {}).get("transferku")
            if tk_cfg:
                all_banks_provider_template["transferku"] = dict(tk_cfg)
                break

    regular_locations: list[dict] = []
    for loc in locations:
        if not is_all_banks_location(loc):
            regular_locations.append(dict(loc))

    matched_major_indices: set[int] = set()

    # Step 1: Detect and handle collisions with existing banks
    for loc in regular_locations:
        for idx, major in enumerate(major_list):
            if idx in matched_major_indices:
                continue
            if matches_major_bank(loc, major):
                swift_code = major.get("swift", "")
                if swift_code:
                    loc["swift"] = swift_code
                    loc["swift_code"] = swift_code

                if all_banks_provider_template:
                    providers = dict(loc.get("providers") or {})
                    for p_name, p_cfg in all_banks_provider_template.items():
                        if p_name not in providers:
                            providers[p_name] = dict(p_cfg)
                    loc["providers"] = providers

                matched_major_indices.add(idx)
                break

    # Step 2: Add uncollided major banks
    new_locations: list[dict] = []
    for idx, major in enumerate(major_list):
        if idx in matched_major_indices:
            continue
        swift_code = major.get("swift", "")
        canonical_id = (
            f"{country_iso}_{swift_code}"
            if swift_code
            else f"{country_iso}_{re.sub(r'[^A-Z0-9]', '', major['name'].upper())}"
        )

        providers: dict[str, Any] = {
            p_name: dict(p_cfg)
            for p_name, p_cfg in all_banks_provider_template.items()
        }

        new_entry = {
            "canonical_id": canonical_id,
            "name": major["name"],
            "swift": swift_code,
            "swift_code": swift_code,
            "providers": providers,
        }
        new_locations.append(new_entry)

    # Step 3: Combine regular locations, new major banks, and all-banks entries
    result = regular_locations + new_locations
    if keep_all_banks:
        result.extend(all_banks_entries)

    return result


async def _build_locations(
    iso_code: str,
    payment_mode: str = "B",
    transaction_type: str | None = None,
    similarity_threshold: float = 0.65,
) -> dict:
    """
    Fetches locations from both Transferku and LightRemit for a given country ISO code.
    - Transferku uses POST /v1/payers with {"iso_code": iso_code} and parses location_detail from transaction_types
    - LightRemit uses POST GetAgentList with {"paymentMode": payment_mode, "payoutCountry": iso_code}

    Applies similarity matching to deduplicate locations between providers so banks
    existing in both LightRemit and Transferku are not duplicated.
    Returns a combined dictionary containing location lists and status/errors from both providers.
    """
    country_iso = iso_code.strip().upper()

    async def _get_lightremit_locations():
        try:
            banks = await fetch_bank_list(payout_country=country_iso, payment_mode=payment_mode)
            return {"locations": banks, "error": None}
        except Exception as e:
            return {"locations": [], "error": str(e)}

    async def _get_transferku_locations():
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
                payers = await fetch_payers_for_country(client=client, iso_code=country_iso)
            if not payers:
                return {"locations": [], "error": "Payers not found"}
            valid_payers = [p for p in payers if payer_has_valid_transaction_type(p, iso_code=country_iso)]
            extracted = extract_transferku_locations(valid_payers, transaction_type=transaction_type)
            return {"locations": extracted, "error": None}
        except Exception as e:
            return {"locations": [], "error": str(e)}

    lr_result, tk_result = await asyncio.gather(
        _get_lightremit_locations(),
        _get_transferku_locations(),
        return_exceptions=True,
    )

    if isinstance(lr_result, Exception):
        lr_result = {"locations": [], "error": str(lr_result)}
    if isinstance(tk_result, Exception):
        tk_result = {"locations": [], "error": str(tk_result)}

    lr_locations = lr_result.get("locations") or []
    tk_locations = tk_result.get("locations") or []
    candidates = build_mapping_candidates(lr_locations, tk_locations)
    mapping = {
        row["tk"]: row["lr"]
        for row in candidates
        if row["status"] == "approved"
    }
    merged_locations = merge_locations(lr_locations, tk_locations, mapping)
    enriched_locations = enrich_locations_with_major_banks(merged_locations, country_iso)
    return {
        "iso_code": country_iso,
        "locations": enriched_locations,
        "total": len(enriched_locations),
        "errors": {
            "lightremit": lr_result["error"],
            "transferku": tk_result["error"],
        },
    }



LOCATIONS_TTL = 6 * 3600             # fresh copy: both providers answered
LOCATIONS_PARTIAL_TTL = 120          # one provider failed: retry soon
LOCATIONS_STALE_TTL = 7 * 24 * 3600  # last-known-good copy, used if both providers fail
LOCK_TTL = 30                        # seconds, prevents several workers rebuilding at once


def _keys(iso: str, mode: str, tx: str) -> dict[str, str]:
    base = f"locations:{iso}:{mode}:{tx}"
    return {
        "fresh": base,                  # full JSON payload
        "stale": f"{base}:stale",       # long-lived backup
        "idx": f"{base}:idx",           # hash: canonical_id -> bank JSON
        "lock": f"{base}:lock",
    }


async def _read_json(r, key: str) -> dict | None:
    try:
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except (RedisError, ValueError) as e:
        logger.warning("locations cache read failed key=%s err=%r", key, e)
        return None


async def _write_cache(r, keys: dict[str, str], payload: dict, ttl: int, keep_stale: bool) -> None:
    """Store the full payload and a per-bank index so quotes can look up one bank in O(1)."""
    try:
        blob = json.dumps(payload, ensure_ascii=False)
        async with r.pipeline(transaction=True) as pipe:
            pipe.set(keys["fresh"], blob, ex=ttl)
            if keep_stale:
                pipe.set(keys["stale"], blob, ex=LOCATIONS_STALE_TTL)
            pipe.delete(keys["idx"])
            if payload["locations"]:
                pipe.hset(
                    keys["idx"],
                    mapping={
                        b["canonical_id"]: json.dumps(b, ensure_ascii=False)
                        for b in payload["locations"]
                    },
                )
                pipe.expire(keys["idx"], ttl)
            await pipe.execute()
    except RedisError as e:
        logger.warning("locations cache write failed key=%s err=%r", keys["fresh"], e)


async def fetch_locations_from_both(
    iso_code: str,
    payment_mode: str = "B",
    transaction_type: str | None = "C2C",
    force_refresh: bool = False,
) -> dict:
    iso = iso_code.strip().upper()
    tx = (transaction_type or "C2C").upper()
    keys = _keys(iso, payment_mode, tx)
    r = await get_redis_client()

    # 1. Fresh cache hit
    if not force_refresh:
        cached = await _read_json(r, keys["fresh"])
        if cached:
            return {**cached, "source": "cache"}

    # 2. Single-flight: only one worker rebuilds, the others wait for its result
    got_lock = False
    try:
        got_lock = bool(await r.set(keys["lock"], "1", nx=True, ex=LOCK_TTL))
    except RedisError as e:
        logger.warning("locations lock failed err=%r", e)
        got_lock = True  # Redis trouble: just build without the lock

    if not got_lock:
        for _ in range(20):                 # wait up to ~5s
            await asyncio.sleep(0.25)
            cached = await _read_json(r, keys["fresh"])
            if cached:
                return {**cached, "source": "cache"}
        # the other worker didn't finish: build it ourselves

    try:
        # 3. Build live
        payload = await _build_locations(
            iso_code=iso, payment_mode=payment_mode, transaction_type=tx
        )
        payload["cached_at"] = datetime.now(timezone.utc).isoformat()
        errors = payload["errors"]
        n_failed = sum(1 for e in errors.values() if e)

        if n_failed == 0 and payload["locations"]:
            await _write_cache(r, keys, payload, LOCATIONS_TTL, keep_stale=True)
            return {**payload, "source": "live"}

        if n_failed == 1 and payload["locations"]:
            # Partial result: cache briefly so we don't hammer the failing provider,
            # but don't overwrite the good stale copy.
            await _write_cache(r, keys, payload, LOCATIONS_PARTIAL_TTL, keep_stale=False)
            return {**payload, "source": "live"}

        # 4. Everything failed (or empty): fall back to the last good copy
        stale = await _read_json(r, keys["stale"])
        if stale:
            logger.warning("serving stale locations iso=%s tx=%s errors=%s", iso, tx, errors)
            await _write_cache(r, keys, stale, LOCATIONS_PARTIAL_TTL, keep_stale=False)
            return {**stale, "errors": errors, "source": "stale"}

        return {**payload, "source": "live"}  # nothing to fall back to; caller sees the errors
    finally:
        if got_lock:
            try:
                await r.delete(keys["lock"])
            except RedisError:
                pass


async def get_bank(
    iso_code: str,
    canonical_id: str,
    payment_mode: str = "B",
    transaction_type: str = "C2C",
) -> dict:
    """Used by the quote endpoint: returns one merged bank (with its `providers` block)."""
    iso = iso_code.strip().upper()
    tx = (transaction_type or "C2C").upper()
    keys = _keys(iso, payment_mode, tx)
    r = await get_redis_client()

    raw = None
    try:
        raw = await r.hget(keys["idx"], canonical_id)
        if raw is None and not await r.exists(keys["idx"]):
            # index expired or never built: rebuild once, then retry
            await fetch_locations_from_both(iso, payment_mode, tx)
            raw = await r.hget(keys["idx"], canonical_id)
    except RedisError as e:
        logger.warning("get_bank redis failed err=%r", e)

    if raw is None:
        # Redis down or bank missing: fall back to the payload itself
        data = await fetch_locations_from_both(iso, payment_mode, tx)
        for b in data["locations"]:
            if b["canonical_id"] == canonical_id:
                return b
        raise HTTPException(status_code=404, detail="Unknown bank. Please reload the bank list.")

    return json.loads(raw)


async def invalidate_locations(iso_code: str, payment_mode: str = "B", transaction_type: str = "C2C") -> None:
    """Call after you approve new rows in the mapping table, so the merged list is rebuilt."""
    keys = _keys(iso_code.strip().upper(), payment_mode, (transaction_type or "C2C").upper())
    r = await get_redis_client()
    await r.delete(keys["fresh"], keys["idx"])   # keep :stale as the safety net
    
# Aliases
fetch_locations = fetch_locations_from_both
fetch_locations_transferku_and_lightremit = fetch_locations_from_both

