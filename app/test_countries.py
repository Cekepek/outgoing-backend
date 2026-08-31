"""
Fetch countries from {host}/v1/countries, then for each country check
{host}/v1/payers?country_iso_code=... to determine whether that country
has at least one usable payer.

A payer is considered "usable" for a country if:
  - the /v1/payers call does NOT return the "Payers not found" status
    (status_message: "Payers not found for this country.")
  - AND at least one of its transaction_types has
    maximum_transaction_amount > 0

If a payer's every transaction_type has maximum_transaction_amount == 0,
that payer doesn't count. If NO payer for the country counts, the
country is excluded from the final list.

Adjust the payers endpoint's query param name/shape to match your actual
LightRemit contract if it differs (e.g. path param vs query param).
"""

import asyncio
import httpx

from app.config import settings  # settings.payment_host_transferku


PAYERS_NOT_FOUND_STATUS = "1000997"


async def fetch_countries(client: httpx.AsyncClient) -> list[dict]:
    try:
        resp = await client.get(f"{settings.payment_host_transferku}/v1/countries",auth=httpx.BasicAuth(settings.payment_client_id_transferku, settings.payment_client_secret_transferku))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(e)
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
            f"{settings.payment_host_transferku}/v1/payers",auth=httpx.BasicAuth(settings.payment_client_id_transferku, settings.payment_client_secret_transferku),
            json={"iso_code": iso_code},
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as e:
        print(f"[ERROR] {iso_code}: {e.response.status_code} - {e.response.text}")
        return
    except Exception as e:
        print(f"[ERROR] {iso_code}: {type(e).__name__}: {e}")
        return
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
                        "name": country["name"],"payer_ids": [p["payer_id"] for p in valid_payers],
                        "payers": valid_payers,
                    }
                )

        await asyncio.gather(*(check_country(c) for c in countries))

        order = {c["iso_code"]: i for i, c in enumerate(countries)}
        results.sort(key=lambda r: order[r["iso_code"]])

        return results


if __name__ == "__main__":
    output = asyncio.run(get_countries_with_valid_payers())
    for country in output:
        print(
            f"{country['iso_code']} ({country['name']}) - "
            f"payer_ids: {country['payer_ids']}"
        )