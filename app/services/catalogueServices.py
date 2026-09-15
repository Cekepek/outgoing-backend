import asyncio
from datetime import datetime
import difflib
import json
import random
import re
import string
from fastapi import HTTPException
import httpx
from app.utils.signature import build_request
from app.utils.redis import redis_client
from app.config import settings


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

async def best_bank_for_country(
    payout_country: str,
    payout_currency: str,
    transfer_amount: str,
    calc_by: str,
    payment_mode: str,
) -> tuple[dict | None, str | None]:
    try:
        banks = await fetch_bank_list(payout_country, payment_mode=payment_mode)
    except Exception as e:
        print(f"[WARN best_bank_for_country] fetch_bank_list failed: {e}")
        return None, str(e)

    # exclude aggregate "ALL BANKS" style entries — not a real payout bank
    real_banks = [
        b for b in banks
        if (b.get("data") or b.get("locationId")) != f"{payout_country[:3].upper()}ALL"
    ]
    if not real_banks:
        return None, "No banks found for this country"

    rate_results = await asyncio.gather(*[
        fetch_rate(
            transfer_amount=transfer_amount,
            calc_by=calc_by,
            payout_currency=payout_currency,
            payment_mode=payment_mode,
            location_id=bank.get("data") or bank.get("locationId"),
            payout_country=payout_country,
        )
        for bank in real_banks
    ], return_exceptions=True)

    candidates = []
    errors = []
    for bank, result in zip(real_banks, rate_results):
        if isinstance(result, Exception):
            errors.append(str(result))
            continue
        if isinstance(result, tuple):
            rate, err = result
            if rate is not None:
                candidates.append({"bank": bank, "rate": rate})
            elif err:
                errors.append(err)
        elif result is not None:
            candidates.append({"bank": bank, "rate": result})

    if not candidates:
        err_msg = errors[0] if errors else "No exchange rate available"
        return None, err_msg

    # payoutAmount is already net of serviceCharge/vatCharge — best single metric
    best = max(candidates, key=lambda c: float(c["rate"]["payoutAmount"]))
    return best, None
    
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


REDIS_TRANSFERKU_VALID_COUNTRIES_KEY = "transferku:valid_countries"


async def get_countries_with_valid_payers(force_refresh: bool = False) -> list[dict]:
    # 1. Check if the key exists in Redis first
    if not force_refresh:
        try:
            cached_data = await redis_client.get(REDIS_TRANSFERKU_VALID_COUNTRIES_KEY)
            if cached_data:
                print("[INFO] Retrieved valid Transferku regions & payers from Redis cache")
                return json.loads(cached_data)
        except Exception as e:
            print(f"[WARN Redis get] Failed to read cache: {e}")

    # 2. Key does not exist -> Fetch from Transferku APIs
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

    # 3. Save valid regions and payer_id data into Redis
    if results:
        try:
            await redis_client.set(
                REDIS_TRANSFERKU_VALID_COUNTRIES_KEY,
                json.dumps(results),
                ex=settings.redis_cache_ttl,
            )
            print("[INFO] Saved valid Transferku regions & payers into Redis cache")
        except Exception as e:
            print(f"[WARN Redis set] Failed to save cache: {e}")

    return results


async def fetch_quote_transferku(
    client: httpx.AsyncClient,
    payer_id: str,
    payout_country: str,
    payout_currency: str,
    amount: float | int,
    mode: str = "DESTINATION_AMOUNT",
) -> tuple[dict | None, str | None]:
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


def _format_error_message(lightremit_error: str | None, transferku_error: str | None = None) -> str:
    if lightremit_error and "service charge is not defined" in lightremit_error.lower():
        return "the amount is too high or too low"
    if lightremit_error:
        return lightremit_error
    if transferku_error:
        return transferku_error
    return "Data not found"


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


def extract_transferku_location_candidates(
    payers: list[dict],
    transaction_type: str | None = None,
) -> list[dict]:
    """
    Extracts all searchable location/bank candidates from Transferku payers
    along with their associated payer_id and transaction_type.
    """
    candidates: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for payer in payers:
        pid = str(payer.get("payer_id", ""))
        pname = payer.get("name", "")
        tx_types = payer.get("transaction_types", {})
        if not isinstance(tx_types, dict):
            continue

        types_to_check = (
            {transaction_type: tx_types[transaction_type]}
            if (transaction_type and transaction_type in tx_types)
            else tx_types
        )
        has_locations = False
        for tx_key, tx_val in types_to_check.items():
            if not isinstance(tx_val, dict):
                continue
            for loc in tx_val.get("location_detail", []):
                has_locations = True
                loc_id = loc.get("locationId") or ""
                loc_name = loc.get("locationName") or pname
                key = (pid, loc_id, loc_name)
                if key not in seen:
                    seen.add(key)
                    candidates.append({
                        "payer_id": pid,
                        "payer_name": pname,
                        "location_id": loc_id,
                        "location_name": loc_name,
                        "transaction_type": tx_key,
                        "optional_field": loc.get("optionalField", ""),
                    })

        # If no explicit location_detail list exists, use payer info as candidate
        if not has_locations and pname:
            key = (pid, "", pname)
            if key not in seen:
                seen.add(key)
                candidates.append({
                    "payer_id": pid,
                    "payer_name": pname,
                    "location_id": "",
                    "location_name": pname,
                    "transaction_type": transaction_type or "C2C",
                    "optional_field": "",
                })

    return candidates


async def find_similar_locations_in_country(
    payout_country: str,
    payment_mode: str = "B",
    transaction_type: str | None = None,
    similarity_threshold: float = 0.65,
) -> list[dict]:
    """
    Finds all matched/similar locations available in both LightRemit and Transferku
    for a given country.
    """
    country_iso = payout_country.strip().upper()

    async def _get_lr():
        try:
            banks = await fetch_bank_list(country_iso, payment_mode=payment_mode)
            return [b for b in banks if (b.get("data") or b.get("locationId") or b.get("value")) != f"{country_iso[:3].upper()}ALL"]
        except Exception:
            return []

    async def _get_tk():
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                payers = await fetch_payers_for_country(client, country_iso)
            return extract_transferku_location_candidates(payers or [], transaction_type=transaction_type)
        except Exception:
            return []

    lr_banks, tk_candidates = await asyncio.gather(_get_lr(), _get_tk())

    matches: list[dict] = []
    for lr in lr_banks:
        lr_name = lr.get("description") or lr.get("locationName") or ""
        best_tk = None
        best_score = 0.0
        for tk in tk_candidates:
            score = max(
                calculate_location_similarity(lr_name, tk.get("location_name")),
                calculate_location_similarity(lr_name, tk.get("payer_name")),
            )
            if score > best_score:
                best_score = score
                best_tk = tk

        if best_tk and best_score >= similarity_threshold:
            matches.append({
                "lightremit": lr,
                "transferku": best_tk,
                "similarity_score": round(best_score, 3),
                "matched_name": lr_name,
            })

    return matches


async def select_best_rate_by_similar_location(
    payout_country: str,
    payout_currency: str,
    transfer_amount: float | str,
    location_name: str | None = None,
    location_id: str | None = None,
    calc_by: str = "P",
    payment_mode: str = "B",
    transaction_type: str | None = None,
    similarity_threshold: float = 0.65,
) -> tuple[dict | None, str | None]:
    """
    Selects the best rate between Transferku and LightRemit when matching/similar locations
    (e.g., locationName is 'BANK OF CHINA') exist in both providers.

    Compares payout amounts (for source fixed 'C') or collect amounts (for destination fixed 'P')
    to determine the best provider for the response.
    """
    country_iso = payout_country.strip().upper()
    amount_float = float(transfer_amount) if isinstance(transfer_amount, (str, int, float)) else transfer_amount

    # 1. Fetch LightRemit banks and Transferku payers concurrently
    async def _get_lr_banks():
        try:
            banks = await fetch_bank_list(country_iso, payment_mode=payment_mode)
            real_banks = [
                b for b in banks
                if (b.get("data") or b.get("locationId") or b.get("value")) != f"{country_iso[:3].upper()}ALL"
            ]
            return real_banks, None
        except Exception as e:
            return [], str(e)

    async def _get_tk_payers():
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                payers = await fetch_payers_for_country(client, country_iso)
            if not payers:
                return [], "Payers not found"
            candidates = extract_transferku_location_candidates(payers, transaction_type=transaction_type)
            return candidates, None
        except Exception as e:
            return [], str(e)

    (lr_banks, lr_bank_err), (tk_candidates, tk_cand_err) = await asyncio.gather(
        _get_lr_banks(),
        _get_tk_payers(),
    )

    # 2. Match location in LightRemit
    matched_lr_bank = None
    if lr_banks:
        if location_id:
            matched_lr_bank = next(
                (b for b in lr_banks if (b.get("value") or b.get("locationId")) == location_id),
                None
            )
        if not matched_lr_bank and location_name:
            scored_lr = [
                (calculate_location_similarity(b.get("description") or b.get("locationName"), location_name), b)
                for b in lr_banks
            ]
            scored_lr.sort(key=lambda x: x[0], reverse=True)
            if scored_lr and scored_lr[0][0] >= similarity_threshold:
                matched_lr_bank = scored_lr[0][1]

    # 3. Match location in Transferku
    matched_tk_cand = None
    if tk_candidates:
        if location_id:
            matched_tk_cand = next(
                (c for c in tk_candidates if c.get("location_id") == location_id),
                None
            )
        if not matched_tk_cand and location_name:
            scored_tk = [
                (
                    max(
                        calculate_location_similarity(c.get("location_name"), location_name),
                        calculate_location_similarity(c.get("payer_name"), location_name),
                    ),
                    c
                )
                for c in tk_candidates
            ]
            scored_tk.sort(key=lambda x: x[0], reverse=True)
            if scored_tk and scored_tk[0][0] >= similarity_threshold:
                matched_tk_cand = scored_tk[0][1]

    # If neither specific match succeeded and no location was provided, attempt matching any similar location
    if not matched_lr_bank and not matched_tk_cand and not location_name and not location_id:
        if lr_banks and tk_candidates:
            similar_pairs = await find_similar_locations_in_country(
                payout_country=country_iso,
                payment_mode=payment_mode,
                transaction_type=transaction_type,
                similarity_threshold=similarity_threshold,
            )
            if similar_pairs:
                matched_lr_bank = similar_pairs[0]["lightremit"]
                matched_tk_cand = similar_pairs[0]["transferku"]

    # 4. Fetch rates concurrently for matched locations
    async def _fetch_lr_rate():
        if not matched_lr_bank:
            return None, lr_bank_err or "No matching LightRemit bank found"
        loc_id = matched_lr_bank.get("value") or matched_lr_bank.get("locationId")
        return await fetch_rate(
            transfer_amount=str(amount_float),
            calc_by=calc_by,
            payout_currency=payout_currency,
            payment_mode=payment_mode,
            location_id=loc_id,
            payout_country=country_iso,
        )

    async def _fetch_tk_rate():
        if not matched_tk_cand:
            return None, tk_cand_err or "No matching Transferku payer found"
        payer_id = matched_tk_cand.get("payer_id")
        mode = "SOURCE_AMOUNT" if calc_by == "C" else "DESTINATION_AMOUNT"
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await fetch_quote_transferku(
                client=client,
                payer_id=payer_id,
                payout_country=country_iso,
                payout_currency=payout_currency,
                amount=amount_float,
                mode=mode,
            )

    (lr_rate_payload, lr_rate_err), (tk_quote_payload, tk_quote_err) = await asyncio.gather(
        _fetch_lr_rate(),
        _fetch_tk_rate(),
    )

    # 5. Handle cases where one or both failed
    if lr_rate_payload is None and tk_quote_payload is None:
        err_msg = _format_error_message(lr_rate_err or lr_bank_err, tk_quote_err or tk_cand_err)
        return None, err_msg

    # Extract amounts
    lr_collect = 0.0
    lr_payout = 0.0
    if lr_rate_payload:
        try:
            lr_collect = float(lr_rate_payload.get("collectAmount", 0))
        except (ValueError, TypeError):
            pass
        try:
            lr_payout = float(lr_rate_payload.get("payoutAmount", 0))
        except (ValueError, TypeError):
            pass

    tk_source, tk_dest = _extract_transferku_amounts(tk_quote_payload) if tk_quote_payload else (0.0, 0.0)

    # 6. Compare rates and select winner
    if lr_rate_payload is not None and tk_quote_payload is not None:
        if calc_by == "C":
            # Fixed source collect amount: choose whichever gives highest destination payout amount
            if tk_dest > lr_payout:
                chosen_agent = "TRANSFERKU"
            else:
                chosen_agent = "LIGHTREMIT"
        else:
            # Fixed destination payout amount (calc_by == "P"):
            # Choose whichever requires lower IDR collect amount
            if tk_source > 0 and lr_collect > 0:
                if tk_source < lr_collect:
                    chosen_agent = "TRANSFERKU"
                else:
                    chosen_agent = "LIGHTREMIT"
            elif tk_source > 0:
                chosen_agent = "TRANSFERKU"
            else:
                chosen_agent = "LIGHTREMIT"
    elif tk_quote_payload is not None:
        chosen_agent = "TRANSFERKU"
    else:
        chosen_agent = "LIGHTREMIT"

    resolved_loc_name = (
        location_name
        or (matched_lr_bank.get("description") if matched_lr_bank else None)
        or (matched_tk_cand.get("location_name") if matched_tk_cand else None)
    )

    response_data = {
        "chosen_agent": chosen_agent,
        "location_name": resolved_loc_name,
        "payout_country": country_iso,
        "payout_currency": payout_currency,
        "transfer_amount": amount_float,
        "calc_by": calc_by,
        "matched_location": {
            "lightremit": matched_lr_bank,
            "transferku": matched_tk_cand,
        },
        "rate_comparison": {
            "lightremit": {
                "collect_amount": lr_collect,
                "payout_amount": lr_payout,
                "rate": lr_rate_payload,
                "error": lr_rate_err,
            },
            "transferku": {
                "collect_amount": tk_source,
                "payout_amount": tk_dest,
                "quote": tk_quote_payload,
                "error": tk_quote_err,
            },
        },
        # Standard fields for backwards-compatibility
        "bank": matched_lr_bank if chosen_agent == "LIGHTREMIT" else None,
        "rate": lr_rate_payload if chosen_agent == "LIGHTREMIT" else None,
        "quote": tk_quote_payload if chosen_agent == "TRANSFERKU" else None,
    }

    return response_data, None


# Aliases for similar location rate selection
select_best_rate_by_location = select_best_rate_by_similar_location
compare_rate_for_similar_location = select_best_rate_by_similar_location
get_best_rate_for_location = select_best_rate_by_similar_location


async def compare_transferku_and_lightremit(
    payout_country: str,
    payout_currency: str,
    transfer_amount: float | str,
    calc_by: str = "P",
    payment_mode: str = "B",
    location_name: str | None = None,
    location_id: str | None = None,
    transaction_type: str | None = None,
    similarity_threshold: float = 0.65,
) -> tuple[dict | None, str | None]:
    # If a specific location is requested, route to location-specific rate selector
    if location_name or location_id:
        return await select_best_rate_by_similar_location(
            payout_country=payout_country,
            payout_currency=payout_currency,
            transfer_amount=transfer_amount,
            location_name=location_name,
            location_id=location_id,
            calc_by=calc_by,
            payment_mode=payment_mode,
            transaction_type=transaction_type,
            similarity_threshold=similarity_threshold,
        )

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

    lr_response, valid_countries = await asyncio.gather(
        lightremit_task,
        valid_countries_task,
        return_exceptions=True
    )

    lightremit_result = None
    lightremit_error = None
    if isinstance(lr_response, Exception):
        print(f"[WARN compare_transferku_and_lightremit] LightRemit failed: {lr_response}")
        lightremit_error = str(lr_response)
    elif isinstance(lr_response, tuple):
        lightremit_result, lightremit_error = lr_response
    elif lr_response is not None:
        lightremit_result = lr_response

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
            }, None
        return None, _format_error_message(lightremit_error, "Country not supported by Transferku")

    # 3. Country is supported by Transferku -> fetch quote(s) from /v1/quotes
    payer_ids = matching_country.get("payer_ids", [])
    transferku_quote = None
    transferku_errors = []

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

        valid_quotes = []
        for q in quote_results:
            if isinstance(q, Exception):
                transferku_errors.append(str(q))
            elif isinstance(q, tuple):
                quote, err = q
                if quote:
                    valid_quotes.append(quote)
                elif err:
                    transferku_errors.append(err)
            elif q:
                valid_quotes.append(q)

        if valid_quotes:
            # Pick best Transferku quote (lowest source amount if destination amount mode)
            transferku_quote = min(
                valid_quotes,
                key=lambda q: _extract_transferku_amounts(q)[0] or float("inf")
            )

    # 4. Compare results
    if transferku_quote is None and lightremit_result is None:
        tk_err = transferku_errors[0] if transferku_errors else None
        return None, _format_error_message(lightremit_error, tk_err)

    if transferku_quote is None:
        return {
            "chosen_agent": "LIGHTREMIT",
            "bank": lightremit_result.get("bank"),
            "rate": lightremit_result.get("rate"),
        }, None

    if lightremit_result is None:
        return {
            "chosen_agent": "TRANSFERKU",
            "quote": transferku_quote,
        }, None

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
        }, None
    else:
        return {
            "chosen_agent": "LIGHTREMIT",
            "bank": lightremit_result.get("bank"),
            "rate": lightremit_result.get("rate"),
        }, None
async def get_transferku_purpose_of_remittance(
    iso_code: str,
    payer_id: str,
    transaction_type: str | None = None,
) -> tuple[list[str] | None, str | None]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        payers = await fetch_payers_for_country(client, iso_code)

    if payers is None:
        return None, f"Payers not found for country '{iso_code}'"

    # Find the requested payer by payer_id
    matched_payer = next(
        (p for p in payers if str(p.get("payer_id")) == str(payer_id)),
        None
    )
    if not matched_payer:
        return None, f"Payer '{payer_id}' not found for country '{iso_code}'"

    transaction_types = matched_payer.get("transaction_types", {})
    if not transaction_types:
        return None, f"No transaction types found for payer '{payer_id}'"

    # If specific transaction_type is specified (e.g. C2C, C2B, B2C, B2B)
    if transaction_type:
        tx_info = transaction_types.get(transaction_type)
        if not tx_info:
            return None, f"Transaction type '{transaction_type}' not found for payer '{payer_id}'"
        return tx_info.get("purpose_of_remittance_values_accepted", []), None

    # If transaction_type is not specified, collect all unique purpose values across transaction types
    purposes: list[str] = []
    seen: set[str] = set()
    for tx_type, tx_info in transaction_types.items():
        if isinstance(tx_info, dict):
            for val in tx_info.get("purpose_of_remittance_values_accepted", []):
                if val not in seen:
                    seen.add(val)
                    purposes.append(val)

    return purposes, None


# Alias for backward compatibility
get_transferku_catalogue = get_transferku_purpose_of_remittance


def extract_transferku_locations(
    payers: list[dict],
    transaction_type: str | None = None,
) -> list[dict]:
    """
    Extracts location details from Transferku payers.
    If transaction_type is provided (e.g. 'C2C', 'C2B', 'B2C', 'B2B'),
    extracts location_detail for that specific transaction type.
    Otherwise, extracts and deduplicates location_detail across all transaction types.
    """
    locations: list[dict] = []
    seen_ids: set[str] = set()

    for payer in payers:
        tx_types = payer.get("transaction_types", {})
        if not isinstance(tx_types, dict):
            continue

        if transaction_type:
            types_to_check = {transaction_type: tx_types.get(transaction_type)} if transaction_type in tx_types else {}
        else:
            types_to_check = tx_types

        for tx_key, tx_val in types_to_check.items():
            if not isinstance(tx_val, dict):
                continue
            for loc in tx_val.get("location_detail", []):
                loc_id = loc.get("locationId")
                if loc_id and loc_id not in seen_ids:
                    seen_ids.add(loc_id)
                    locations.append({
                        "value": loc.get("locationId"),
                        "description": loc.get("locationName"),
                        "optionalField": loc.get("optionalField", ""),
                    })
                elif not loc_id:
                    locations.append(loc)

    return locations


async def fetch_locations_from_both(
    iso_code: str,
    payment_mode: str = "B",
    transaction_type: str | None = None,
) -> dict:
    """
    Fetches locations from both Transferku and LightRemit for a given country ISO code.
    - Transferku uses POST /v1/payers with {"iso_code": iso_code} and parses location_detail from transaction_types
    - LightRemit uses POST GetAgentList with {"paymentMode": payment_mode, "payoutCountry": iso_code}

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
            async with httpx.AsyncClient(timeout=15.0) as client:
                payers = await fetch_payers_for_country(client=client, iso_code=country_iso)
            if not payers:
                return {"locations": [], "error": "Payers not found"}
            extracted = extract_transferku_locations(payers, transaction_type=transaction_type)
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

    return {
        "iso_code": country_iso,
        "lightremit": {
            "locations": lr_result["locations"],
            "total": len(lr_result["locations"]),
            "error": lr_result["error"],
        },
        "transferku": {
            "locations": tk_result["locations"],
            "total": len(tk_result["locations"]),
            "error": tk_result["error"],
        },
    }


# Aliases
fetch_locations = fetch_locations_from_both
fetch_locations_transferku_and_lightremit = fetch_locations_from_both

