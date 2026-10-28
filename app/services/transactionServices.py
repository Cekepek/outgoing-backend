import random
import string
from datetime import date, datetime, timezone
from typing import Any, Optional

import httpx
from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models import Sender
from app.schemas import (
    SendTransactionRequest,
    TransferkuBusinessCustomerRequest,
    TransferkuCustomerRequest,
)
from app.services.catalogueServices import fetch_payers_for_country
from app.services.idTypeService import to_transferku_id_type
from app.services.schemasService import build_lightremit_payload
from app.utils.redis import redis_client
from app.utils.signature import build_request, generate_agent_txn_id

# In-memory fallback cache for customer_id
CUSTOMER_ID_CACHE: dict[str, str] = {}


def generate_transferku_external_id() -> str:
    """
    Generates a unique 24-digit external ID (timestamp YYYYMMDDHHMMSS + 10 random digits).
    Example: '202512180000002841111254'
    """
    return datetime.now().strftime("%Y%m%d%H%M%S") + "".join(random.choices(string.digits, k=6))


def _format_date(d: Any) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y-%m-%d")
    return str(d) if d else ""


def _map_gender(gender: str | None) -> str:
    if not gender:
        return "MALE"
    g = gender.strip().upper()
    if g in ("M", "MALE", "L", "LAKI-LAKI"):
        return "MALE"
    if g in ("F", "FEMALE", "P", "PEREMPUAN"):
        return "FEMALE"
    return g


def get_sender_from_db(
    db: Session,
    sender_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> Sender:
    """
    Queries sender data directly from the database by sender_id or user_id.
    """
    query = db.query(Sender)
    if sender_id is not None:
        sender = query.filter(Sender.id == sender_id).first()
    elif user_id is not None:
        sender = query.filter(Sender.user_id == user_id).first()
    else:
        sender = query.first()

    if not sender:
        raise HTTPException(status_code=404, detail="Sender not found in database")
    return sender


async def save_customer_id(
    customer_id: str,
    sender_id: Optional[int] = None,
    user_id: Optional[int] = None,
    id_number: Optional[str] = None,
    external_id: Optional[str] = None,
) -> None:
    """
    Saves customer_id for a sender in both in-memory cache and Redis.
    """
    if sender_id is not None:
        CUSTOMER_ID_CACHE[f"sender:{sender_id}"] = customer_id
    if user_id is not None:
        CUSTOMER_ID_CACHE[f"user:{user_id}"] = customer_id
    if id_number is not None:
        CUSTOMER_ID_CACHE[f"id_number:{id_number}"] = customer_id
    if external_id is not None:
        CUSTOMER_ID_CACHE[f"external_id:{external_id}"] = customer_id
    CUSTOMER_ID_CACHE["latest_sender"] = customer_id

    try:
        ttl = getattr(settings, "redis_cache_ttl", 86400 * 7)
        if sender_id is not None:
            await redis_client.set(f"transferku:customer:sender:{sender_id}", customer_id, ex=ttl)
        if user_id is not None:
            await redis_client.set(f"transferku:customer:user:{user_id}", customer_id, ex=ttl)
        if id_number is not None:
            await redis_client.set(f"transferku:customer:id_number:{id_number}", customer_id, ex=ttl)
        if external_id is not None:
            await redis_client.set(f"transferku:customer:external_id:{external_id}", customer_id, ex=ttl)
    except Exception as e:
        print(f"[WARN save_customer_id] Redis caching failed: {e}")


async def get_sender_customer_id(
    sender_id: Optional[int] = None,
    user_id: Optional[int] = None,
    id_number: Optional[str] = None,
    external_id: Optional[str] = None,
) -> Optional[str]:
    """
    Retrieves the stored customer_id for a sender, checking in-memory cache first, then Redis.
    """
    if sender_id is not None and f"sender:{sender_id}" in CUSTOMER_ID_CACHE:
        return CUSTOMER_ID_CACHE[f"sender:{sender_id}"]
    if user_id is not None and f"user:{user_id}" in CUSTOMER_ID_CACHE:
        return CUSTOMER_ID_CACHE[f"user:{user_id}"]
    if id_number is not None and f"id_number:{id_number}" in CUSTOMER_ID_CACHE:
        return CUSTOMER_ID_CACHE[f"id_number:{id_number}"]
    if external_id is not None and f"external_id:{external_id}" in CUSTOMER_ID_CACHE:
        return CUSTOMER_ID_CACHE[f"external_id:{external_id}"]

    try:
        if sender_id is not None:
            cid = await redis_client.get(f"transferku:customer:sender:{sender_id}")
            if cid:
                CUSTOMER_ID_CACHE[f"sender:{sender_id}"] = cid
                return cid
        if user_id is not None:
            cid = await redis_client.get(f"transferku:customer:user:{user_id}")
            if cid:
                CUSTOMER_ID_CACHE[f"user:{user_id}"] = cid
                return cid
        if id_number is not None:
            cid = await redis_client.get(f"transferku:customer:id_number:{id_number}")
            if cid:
                CUSTOMER_ID_CACHE[f"id_number:{id_number}"] = cid
                return cid
        if external_id is not None:
            cid = await redis_client.get(f"transferku:customer:external_id:{external_id}")
            if cid:
                CUSTOMER_ID_CACHE[f"external_id:{external_id}"] = cid
                return cid
    except Exception as e:
        print(f"[WARN get_sender_customer_id] Redis retrieval failed: {e}")

    return CUSTOMER_ID_CACHE.get("latest_sender")


# async def save_beneficiary_customer_id(
#     customer_id: str,
#     id_number: Optional[str] = None,
#     external_id: Optional[str] = None,
#     user_id: Optional[int] = None,
#     registration_number: Optional[str] = None,
# ) -> None:
#     """
#     Saves customer_id for a beneficiary in both in-memory cache and Redis.
#     """
#     if id_number is not None:
#         CUSTOMER_ID_CACHE[f"beneficiary:id_number:{id_number}"] = customer_id
#     if registration_number is not None:
#         CUSTOMER_ID_CACHE[f"beneficiary:registration_number:{registration_number}"] = customer_id
#         CUSTOMER_ID_CACHE[f"beneficiary:id_number:{registration_number}"] = customer_id
#     if external_id is not None:
#         CUSTOMER_ID_CACHE[f"beneficiary:external_id:{external_id}"] = customer_id
#     if user_id is not None:
#         CUSTOMER_ID_CACHE[f"beneficiary:user:{user_id}"] = customer_id
#     CUSTOMER_ID_CACHE["latest_beneficiary"] = customer_id

#     try:
#         ttl = getattr(settings, "redis_cache_ttl", 86400 * 7)
#         if id_number is not None:
#             await redis_client.set(f"transferku:beneficiary:id_number:{id_number}", customer_id, ex=ttl)
#         if registration_number is not None:
#             await redis_client.set(f"transferku:beneficiary:registration_number:{registration_number}", customer_id, ex=ttl)
#             await redis_client.set(f"transferku:beneficiary:id_number:{registration_number}", customer_id, ex=ttl)
#         if external_id is not None:
#             await redis_client.set(f"transferku:beneficiary:external_id:{external_id}", customer_id, ex=ttl)
#         if user_id is not None:
#             await redis_client.set(f"transferku:beneficiary:user:{user_id}", customer_id, ex=ttl)
#     except Exception as e:
#         print(f"[WARN save_beneficiary_customer_id] Redis caching failed: {e}")


# async def get_beneficiary_customer_id(
#     id_number: Optional[str] = None,
#     external_id: Optional[str] = None,
#     user_id: Optional[int] = None,
#     registration_number: Optional[str] = None,
# ) -> Optional[str]:
#     """
#     Retrieves the stored customer_id for a beneficiary, checking in-memory cache first, then Redis.
#     """
#     lookup_id = id_number or registration_number
#     if lookup_id is not None and f"beneficiary:id_number:{lookup_id}" in CUSTOMER_ID_CACHE:
#         return CUSTOMER_ID_CACHE[f"beneficiary:id_number:{lookup_id}"]
#     if registration_number is not None and f"beneficiary:registration_number:{registration_number}" in CUSTOMER_ID_CACHE:
#         return CUSTOMER_ID_CACHE[f"beneficiary:registration_number:{registration_number}"]
#     if external_id is not None and f"beneficiary:external_id:{external_id}" in CUSTOMER_ID_CACHE:
#         return CUSTOMER_ID_CACHE[f"beneficiary:external_id:{external_id}"]
#     if user_id is not None and f"beneficiary:user:{user_id}" in CUSTOMER_ID_CACHE:
#         return CUSTOMER_ID_CACHE[f"beneficiary:user:{user_id}"]

#     try:
#         if lookup_id is not None:
#             cid = await redis_client.get(f"transferku:beneficiary:id_number:{lookup_id}")
#             if cid:
#                 CUSTOMER_ID_CACHE[f"beneficiary:id_number:{lookup_id}"] = cid
#                 return cid
#         if registration_number is not None:
#             cid = await redis_client.get(f"transferku:beneficiary:registration_number:{registration_number}")
#             if cid:
#                 CUSTOMER_ID_CACHE[f"beneficiary:registration_number:{registration_number}"] = cid
#                 return cid
#         if external_id is not None:
#             cid = await redis_client.get(f"transferku:beneficiary:external_id:{external_id}")
#             if cid:
#                 CUSTOMER_ID_CACHE[f"beneficiary:external_id:{external_id}"] = cid
#                 return cid
#         if user_id is not None:
#             cid = await redis_client.get(f"transferku:beneficiary:user:{user_id}")
#             if cid:
#                 CUSTOMER_ID_CACHE[f"beneficiary:user:{user_id}"] = cid
#                 return cid
#     except Exception as e:
#         print(f"[WARN get_beneficiary_customer_id] Redis retrieval failed: {e}")

#     return CUSTOMER_ID_CACHE.get("latest_beneficiary")


def _get_first(sources: list[Any], *keys: str, default: Any = None) -> Any:
    """
    Checks multiple sources (e.g. kwargs, dicts, Pydantic models, ORM instances)
    in order for the first matching key with a non-empty value.
    """
    for src in sources:
        if src is None:
            continue
        is_dict = isinstance(src, dict)
        for k in keys:
            val = src.get(k) if is_dict else getattr(src, k, None)
            if val is not None and val != "":
                return val
    return default


def _resolve_representative_names(sources: list[Any]) -> tuple[str, str]:
    """
    Resolves representative first and last name from sources:
    1. Direct representative_firstname / representative_lastname if present.
    2. Split representative_name if present.
    3. Fallback to firstname/lastname or receiver_first_name/receiver_last_name.
    """
    rep_first = _get_first(sources, "representative_firstname")
    rep_last = _get_first(sources, "representative_lastname")
    if rep_first or rep_last:
        return str(rep_first or ""), str(rep_last or "")

    rep_name = _get_first(sources, "representative_name")
    if rep_name:
        parts = str(rep_name).strip().split(None, 1)
        return parts[0], parts[1] if len(parts) > 1 else ""

    fallback_first = _get_first(sources, "receiver_first_name", "firstname", default="")
    fallback_last = _get_first(sources, "receiver_last_name", "lastname", default="")
    return str(fallback_first), str(fallback_last)


async def _post_transferku_customer(
    body: dict[str, Any],
    client: Optional[httpx.AsyncClient] = None,
    caller_name: str = "customer",
) -> dict:
    """
    Posts the customer body to Transferku /v1/customers API and parses errors consistently.
    Reuses existing client or creates a one-off AsyncClient.
    """
    url = f"{settings.payment_host_transferku}/v1/customers"
    auth = httpx.BasicAuth(
        settings.payment_client_id_transferku,
        settings.payment_client_secret_transferku,
    )

    async def _send(c: httpx.AsyncClient) -> dict:
        response = await c.post(url, json=body, auth=auth, timeout=30.0)
        try:
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError:
            err_text = response.text
            print(f"[ERROR {caller_name}] HTTP {response.status_code}: {err_text}")
            try:
                err_json = response.json()
                msg = err_json.get("message") or err_json.get("status_message") or err_text
            except Exception:
                msg = err_text
            raise HTTPException(status_code=response.status_code, detail=f"Transferku error: {msg}")

    if client is not None:
        return await _send(client)
    async with httpx.AsyncClient() as new_client:
        return await _send(new_client)


async def create_sender(
    db: Optional[Session] = None,
    sender_id: Optional[int] = None,
    user_id: Optional[int] = None,
    sender: Optional[Sender] = None,
    payload: Optional[TransferkuCustomerRequest | TransferkuBusinessCustomerRequest | dict] = None,
    client: Optional[httpx.AsyncClient] = None,
    source_of_funds: str = "SALARY",
    **kwargs: Any,
) -> dict:
    """
    Fetches Transferku's '/v1/customers' API to register a sender with sender data
    taken from the database or payload.
    Supports both PERSONAL and BUSINESS customer types.
    Generates a unique 24-digit external_id and persists the resulting customer_id
    so it can be retrieved by send_transaction.
    """
    if sender is None:
        if db is not None:
            sender = get_sender_from_db(db, sender_id=sender_id, user_id=user_id)
        elif sender_id is not None or user_id is not None or payload is None:
            with SessionLocal() as db_session:
                sender = get_sender_from_db(db_session, sender_id=sender_id, user_id=user_id)

    src: Any = sender if sender is not None else (
        payload.model_dump() if isinstance(payload, BaseModel) else payload
    )
    if src is None and not str(kwargs.get("customer_type") or kwargs.get("sender_customer_type") or "").upper() in ("B", "BUSINESS"):
        raise HTTPException(status_code=400, detail="Sender information could not be retrieved")

    sender_id_val = sender.id if sender is not None else (sender_id or kwargs.get("sender_id"))
    user_id_val = sender.user_id if sender is not None else (user_id or kwargs.get("user_id"))
    external_id = kwargs.get("external_id") or generate_transferku_external_id()

    raw_cust_type = _get_first([kwargs, src], "customer_type", "sender_customer_type", default="PERSONAL")
    is_business = str(raw_cust_type).upper() in ("B", "BUSINESS")

    if is_business:
        registered_name = (
            kwargs.get("registered_name")
            or _get_first([src], "registered_name", "sender_company_name")
            or f"{_get_first([kwargs, src], 'representative_firstname', 'firstname', 'sender_first_name', default='')} {_get_first([kwargs, src], 'representative_lastname', 'lastname', 'sender_last_name', default='')}".strip()
            or kwargs.get("registered_name", "")
        )
        trading_name = (
            kwargs.get("trading_name")
            or _get_first([src], "trading_name", "sender_trading_name", "sender_company_name")
            or registered_name
        )
        body = {
            "external_id": external_id,
            "role": _get_first([kwargs, src], "role", default="SENDER"),
            "customer_type": "BUSINESS",
            "registered_name": registered_name,
            "trading_name": trading_name,
            "registration_number": (
                kwargs.get("registration_number")
                or _get_first([src], "registration_number", "sender_company_reg_number", "sender_id_number", "id_number")
                or kwargs.get("id_number", "")
            ),
            "country_iso_code": _get_first([kwargs, src], "country_iso_code", "sender_country", default="IDN"),
            "address": _get_first([kwargs, src], "address", "sender_address", default=""),
            "city": _get_first([kwargs, src], "city", "sender_city", default=""),
            "postal_code": _get_first([kwargs, src], "postal_code", "sender_zip_code", default=""),
            "msisdn": _get_first([kwargs, src], "msisdn", "sender_mobile", default=""),
            "email": _get_first([kwargs, src], "email", "sender_email", default=""),
            "representative_firstname": _get_first([kwargs, src], "representative_firstname", "firstname", "sender_first_name", default=""),
            "representative_lastname": _get_first([kwargs, src], "representative_lastname", "lastname", "sender_last_name", default=""),
            "business_relationship": _get_first([kwargs, src], "business_relationship", "sender_beneficiary_relationship", default="BUSINESS_PARTNER"),
        }
    elif sender is not None:
        body = {
            "external_id": external_id,
            "role": "SENDER",
            "customer_type": "PERSONAL",
            "firstname": sender.sender_first_name or "",
            "lastname": sender.sender_last_name or "",
            "gender": _map_gender(sender.sender_gender),
            "date_of_birth": _format_date(sender.sender_date_of_birth),
            "nationality_country_iso_code": sender.sender_nationality or sender.sender_country or "IDN",
            "country_of_birth_iso_code": sender.sender_nationality or sender.sender_country or "IDN",
            "id_type": to_transferku_id_type(sender.sender_id_type),
            "id_number": sender.sender_id_number or "",
            "id_country_iso_code": sender.sender_id_issue_country or sender.sender_country or "IDN",
            "id_delivery_date": _format_date(sender.sender_id_issue_date),
            "id_expiration_date": _format_date(sender.sender_id_expire_date),
            "country_iso_code": sender.sender_country or "IDN",
            "address": sender.sender_address or "",
            "city": sender.sender_city or "",
            "district": getattr(sender, "district", None),
            "province_state": sender.sender_state or "",
            "postal_code": sender.sender_zip_code or "",
            "msisdn": sender.sender_mobile or "",
            "occupation": sender.sender_occupation or "Marketing researcher",
            "source_of_funds": kwargs.get("source_of_funds", source_of_funds),
        }
    elif isinstance(src, dict):
        body = dict(src)
        body["external_id"] = external_id
        body.setdefault("role", "SENDER")
        body.setdefault("customer_type", "PERSONAL")
    else:
        raise HTTPException(status_code=400, detail="Sender information could not be retrieved")

    for k, v in kwargs.items():
        if k in body and k not in ("external_id", "role"):
            body[k] = v

    data = await _post_transferku_customer(body, client=client, caller_name="create_sender")

    customer_id = data.get("customer_id") or body.get("customer_id")
    if customer_id:
        id_number = body.get("id_number") or body.get("registration_number")
        await save_customer_id(
            customer_id=customer_id,
            sender_id=sender_id_val,
            user_id=user_id_val,
            id_number=id_number,
            external_id=external_id,
        )
        if sender is not None:
            setattr(sender, "transferku_customer_id", customer_id)
        data.setdefault("customer_id", customer_id)

    return data


async def create_beneficiary(
    req: Optional[SendTransactionRequest | TransferkuBusinessCustomerRequest | dict] = None,
    client: Optional[httpx.AsyncClient] = None,
    user_id: Optional[int] = None,
    **kwargs: Any,
) -> dict:
    """
    Fetches Transferku's '/v1/customers' API to register a beneficiary using
    data extracted from SendTransactionRequest (receiver_* fields) or payload dict.
    Supports both PERSONAL and BUSINESS customer types.
    Generates a unique 24-digit external_id and persists the resulting customer_id
    so it can be retrieved by send_transaction.
    """
    external_id = kwargs.get("external_id") or generate_transferku_external_id()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    raw_cust_type = _get_first([kwargs, req], "customer_type", "receiver_customer_type", default="PERSONAL")
    is_business = str(raw_cust_type).upper() in ("B", "BUSINESS")

    if is_business:
        registered_name = (
            kwargs.get("registered_name")
            or _get_first([req], "registered_name", "receiver_company_name")
            or f"{_get_first([req], 'receiver_first_name', 'firstname', default='')} {_get_first([req], 'receiver_last_name', 'lastname', default='')}".strip()
            or _get_first([kwargs, req], "name", default="")
            or kwargs.get("registered_name", "")
        )
        trading_name = (
            kwargs.get("trading_name")
            or _get_first([req], "trading_name", "receiver_company_name")
            or registered_name
        )
        registration_number = (
            kwargs.get("registration_number")
            or _get_first([req], "registration_number", "receiver_company_reg_number", "receiver_id_number", "id_number")
            or kwargs.get("registration_number", "")
        )
        rep_first, rep_last = _resolve_representative_names([kwargs, req])

        body = {
            "external_id": _get_first([kwargs, req], "external_id", default=external_id),
            "role": _get_first([kwargs, req], "role", default="BENEFICIARY"),
            "customer_type": "BUSINESS",
            "registered_name": registered_name,
            "trading_name": trading_name,
            "registration_number": registration_number,
            "country_iso_code": _get_first([kwargs, req], "country_iso_code", "receiver_country", default="CHN"),
            "address": _get_first([kwargs, req], "address", "receiver_address", default=""),
            "city": _get_first([kwargs, req], "city", "receiver_city", "receiver_area_town", default=""),
            "postal_code": _get_first([kwargs, req], "postal_code", "receiver_zip_code", default=""),
            "msisdn": _get_first([kwargs, req], "msisdn", "receiver_contact_number", default=""),
            "email": _get_first([kwargs, req], "email", "receiver_email", default=""),
            "representative_firstname": rep_first,
            "representative_lastname": rep_last,
            "business_relationship": (
                _get_first([kwargs, req], "business_relationship", "sender_beneficiary_relationship", default="BUSINESS_PARTNER")
            ),
        }
    else:
        body = {
            "external_id": external_id,
            "role": "BENEFICIARY",
            "customer_type": "PERSONAL",
            "firstname": _get_first([kwargs, req], "firstname", "receiver_first_name", default=""),
            "lastname": _get_first([kwargs, req], "lastname", "receiver_last_name", default=""),
            "gender": _map_gender(_get_first([kwargs, req], "gender", "receiver_gender", default="MALE")),
            "date_of_birth": _format_date(_get_first([kwargs, req], "date_of_birth", "receiver_date_of_birth", default="")),
            "nationality_country_iso_code": _get_first([kwargs, req], "nationality_country_iso_code", "receiver_nationality", "receiver_country", default="IDN"),
            "id_type": to_transferku_id_type(_get_first([kwargs, req], "id_type", "receiver_id_type")),
            "id_number": _get_first([kwargs, req], "id_number", "receiver_id_number", default="") or kwargs.get("id_number", ""),
            "id_country_iso_code": _get_first([kwargs, req], "id_country_iso_code", "receiver_id_issue_country", "receiver_country", default="AUS"),
            "address": _get_first([kwargs, req], "address", "receiver_address", default=""),
            "city": _get_first([kwargs, req], "city", "receiver_city", "receiver_area_town", default=""),
            "province_state": _get_first([kwargs, req], "province_state", "receiver_state", default=""),
            "postal_code": _get_first([kwargs, req], "postal_code", "receiver_zip_code", default=""),
            "country_iso_code": _get_first([kwargs, req], "country_iso_code", "receiver_country", default="AUS"),
            "msisdn": _get_first([kwargs, req], "msisdn", "receiver_contact_number", default=""),
            "email": _get_first([kwargs, req], "email", "receiver_email", default=""),
            "occupation": _get_first([kwargs, req], "occupation", "receiver_occupation", default="Civil Servant"),
            "source_of_funds": _get_first([kwargs, req], "source_of_funds", "sender_source_of_fund", default="SALARY"),
            "created_at": _get_first([kwargs, req], "created_at", default=now_iso),
            "updated_at": _get_first([kwargs, req], "updated_at", default=now_iso),
        }

    for k, v in kwargs.items():
        if k in body and k not in ("external_id", "role"):
            body[k] = v
    data = await _post_transferku_customer(body, client=client, caller_name="create_beneficiary")

    customer_id = data.get("customer_id") or body.get("customer_id")
    if customer_id:
        id_number = body.get("id_number")
        registration_number = body.get("registration_number")
        # await save_beneficiary_customer_id(
        #     customer_id=customer_id,
        #     id_number=id_number or registration_number,
        #     external_id=external_id,
        #     user_id=user_id or kwargs.get("user_id"),
        #     registration_number=registration_number,
        # )
        data.setdefault("customer_id", customer_id)

    return data


def _format_credit_party_dict(
    data: dict[str, Any],
    field_pool: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}

    # If caller already passed structured type and value, preserve and augment
    if "type" in data and "value" in data:
        formatted = dict(data)
    else:
        formatted = {}
        found_key = None
        target_keys = ("bank_account_number", "account_number", "msisdn", "msidn", "card_number", "iban")
        for k in target_keys:
            if k in data and data[k] is not None and data[k] != "":
                found_key = k
                type_name = "msisdn" if k == "msidn" else k
                formatted["type"] = type_name
                formatted["value"] = str(data[k])
                break

        if not found_key and field_pool:
            for k in target_keys:
                if field_pool.get(k):
                    found_key = k
                    type_name = "msisdn" if k == "msidn" else k
                    formatted["type"] = type_name
                    formatted["value"] = str(field_pool[k])
                    break

        for k, v in data.items():
            if k != found_key and v is not None and v != "":
                formatted[k] = v

    # Attach swift_bic_code if present in field_pool and not already in formatted for bank accounts
    if (
        field_pool
        and "swift_bic_code" not in formatted
        and field_pool.get("swift_bic_code")
        and formatted.get("type") in ("bank_account_number", "account_number", "iban")
    ):
        formatted["swift_bic_code"] = field_pool["swift_bic_code"]

    return formatted


def build_transferku_credit_party_identifier(
    req: SendTransactionRequest,
    payer: Optional[dict[str, Any]] = None,
    accepted_identifiers: Optional[list[Any]] = None,
    transaction_type: Optional[str] = None,
) -> dict[str, Any]:
    """
    Dynamically builds the credit_party_identifier payload to match Transferku's
    credit_party_identifiers_accepted for the specific payer and transaction type,
    formatting primary identifiers with 'type' and 'value'.
    """
    tx_type = (transaction_type or getattr(req, "transaction_type", None) or "C2C").strip().upper()

    # Resolve location_detail if applicable
    loc_id = req.location_id or ""
    loc_name = req.location_name or req.bank_name or ""

    # Auto-resolve locationName from payer's location_detail catalogue if missing
    if payer and not loc_name:
        tx_configs = payer.get("transaction_types", {})
        tx_cfg = tx_configs.get(tx_type) or tx_configs.get("C2C") or (next(iter(tx_configs.values()), {}) if tx_configs else {})
        payer_locations = tx_cfg.get("location_detail", []) if isinstance(tx_cfg, dict) else []
        for loc in payer_locations:
            if str(loc.get("locationId")) == str(loc_id):
                loc_name = loc.get("locationName") or ""
                break
        if not loc_name and payer_locations and not loc_id:
            loc_id = payer_locations[0].get("locationId", "")
            loc_name = payer_locations[0].get("locationName", "")

    location_detail_payload = None
    if loc_id:
        location_detail_payload = {
            "locationId": loc_id,
            "locationName": loc_name or loc_id,
        }

    # Map potential request attributes to Transferku identifier field names
    additional = req.additional_info or {}
    card_number = (
        getattr(req, "card_number", None)
        or additional.get("card_number")
        or (req.bank_account_number if getattr(req, "payment_mode", "") in ("CARD", "C") else None)
    )
    if not card_number and req.bank_account_number:
        card_number = req.bank_account_number

    msisdn = (
        getattr(req, "msisdn", None)
        or additional.get("msisdn")
        or additional.get("msidn")
        or req.receiver_wallet_id_number
        or req.receiver_contact_number
    )

    bank_account_number = req.bank_account_number or additional.get("bank_account_number") or additional.get("account_number")
    bank_branch_name = (
        req.bank_branch_name
        or req.bank_branch_code
        or additional.get("bank_branch_name")
        or additional.get("bank_branch_code")
    )
    swift_bic_code = req.swift_code or getattr(req, "swift_bic_code", None) or additional.get("swift_bic_code") or additional.get("swift_code")
    iban = getattr(req, "iban", None) or additional.get("iban") or (bank_account_number if bank_account_number and bank_account_number[:2].isalpha() else None)

    field_pool: dict[str, Any] = {
        "card_number": card_number,
        "msisdn": msisdn,
        "msidn": msisdn,
        "bank_account_number": bank_account_number,
        "account_number": bank_account_number,
        # "bank_branch_name": bank_branch_name,
        "bsb_code": bank_branch_name,
        # "bank_branch_code": bank_branch_name,
        # "swift_bic_code": swift_bic_code,
        # "swift_code": swift_bic_code,
        "swift": swift_bic_code,
        "iban": iban,
        "location_detail": location_detail_payload,
    }

    # Add any extra keys found in additional_info
    for k, v in additional.items():
        if k not in field_pool and v is not None and v != "":
            field_pool[k] = v

    # 1. If caller supplied an explicit credit_party_identifier dict, format and use it
    if req.credit_party_identifier and isinstance(req.credit_party_identifier, dict):
        cleaned = {k: v for k, v in req.credit_party_identifier.items() if v is not None and v != ""}
        if cleaned:
            return _format_credit_party_dict(cleaned, field_pool)

    # 2. Resolve accepted_identifiers from payer object if not explicitly provided
    if accepted_identifiers is None and payer:
        tx_configs = payer.get("transaction_types", {})
        tx_config = tx_configs.get(tx_type) or tx_configs.get("C2C")
        if not tx_config and tx_configs:
            tx_config = next(iter(tx_configs.values()), {})
        if isinstance(tx_config, dict):
            accepted_identifiers = tx_config.get("credit_party_identifiers_accepted", [])

    # Normalize accepted_identifiers into a list of candidate combinations
    combinations: list[list[str]] = []
    if accepted_identifiers:
        if all(isinstance(x, str) for x in accepted_identifiers):
            combinations = [accepted_identifiers]
        elif all(isinstance(x, list) for x in accepted_identifiers):
            combinations = accepted_identifiers

    # 3. Build identifier based on accepted combination
    if combinations:
        for combo in combinations:
            resolved_combo = {}
            all_matched = True
            for f in combo:
                val = field_pool.get(f)
                if val is not None and val != "":
                    resolved_combo[f] = val
                else:
                    all_matched = False
                    break
            if all_matched:
                return _format_credit_party_dict(resolved_combo, field_pool)

        # Fallback: take the first combination and fill with available fields
        first_combo = combinations[0]
        partial = {f: field_pool[f] for f in first_combo if field_pool.get(f) is not None and field_pool[f] != ""}
        if partial:
            return _format_credit_party_dict(partial, field_pool)

    # 4. Generic fallback if payer or accepted list is not available
    fallback: dict[str, Any] = {}
    if field_pool.get("bank_account_number"):
        fallback["bank_account_number"] = field_pool["bank_account_number"]
        if field_pool.get("bank_branch_name"):
            fallback["bank_branch_name"] = field_pool["bank_branch_name"]
        if location_detail_payload:
            fallback["location_detail"] = location_detail_payload
    elif field_pool.get("card_number"):
        fallback["card_number"] = field_pool["card_number"]
    elif field_pool.get("msisdn"):
        fallback["msisdn"] = field_pool["msisdn"]

    if not fallback:
        for k in ("card_number", "msisdn", "bank_account_number", "iban"):
            if field_pool.get(k):
                fallback[k] = field_pool[k]
                break

    return _format_credit_party_dict(fallback, field_pool)


async def send_transferku_transaction(
    req: SendTransactionRequest,
    sender: Sender,
    client: Optional[httpx.AsyncClient] = None,
    **kwargs: Any,
) -> dict:
    """
    Sends a transfer request to Transferku's '/v1/transfers' API.
    """
    # 1. Resolve quote_id
    quote_id = req.quote_id
    if not quote_id:
        raise HTTPException(
            status_code=400,
            detail="quote_id is required for Transferku transactions. Please obtain a quote first.",
        )

    # 2. Resolve sender_id (Transferku customer_id for sender)
    sender_id = kwargs.get("sender_id") or kwargs.get("sender_customer_id")
    if not sender_id:
        sender_id = await get_sender_customer_id(
            sender_id=sender.id,
            user_id=sender.user_id,
            id_number=sender.sender_id_number,
        )
    if not sender_id:
        sender_res = await create_sender(
            sender=sender,
            client=client,
            source_of_funds=req.sender_source_of_fund,
        )
        sender_id = sender_res.get("customer_id")

    # 3. Resolve beneficiary_id (Transferku customer_id for beneficiary)
    beneficiary_id = kwargs.get("beneficiary_id") or kwargs.get("beneficiary_customer_id")
    # if not beneficiary_id:
    #     beneficiary_id = await get_beneficiary_customer_id(
    #         id_number=req.receiver_id_number or getattr(req, "receiver_company_reg_number", None),
    #         user_id=sender.user_id,
    #         registration_number=getattr(req, "receiver_company_reg_number", None),
    #     )
    if not beneficiary_id:
        bene_res = await create_beneficiary(
            req=req,
            client=client,
            user_id=sender.user_id,
        )
        beneficiary_id = bene_res.get("customer_id")

    # 4. Generate unique external_id
    external_id = generate_transferku_external_id()

    # Resolve payer info if not provided in kwargs
    payer = kwargs.get("payer")
    if not payer and req.receiver_country:
        target_id = str(req.payer_id or kwargs.get("payer_id") or req.location_id or "").strip()
        try:
            if client is not None:
                payers = await fetch_payers_for_country(client, req.receiver_country)
            else:
                async with httpx.AsyncClient(timeout=15.0) as new_client:
                    payers = await fetch_payers_for_country(new_client, req.receiver_country)

            if payers:
                payer = next((p for p in payers if str(p.get("payer_id")) == target_id), None)
                if not payer:
                    for p in payers:
                        for tx in p.get("transaction_types", {}).values():
                            if isinstance(tx, dict) and any(
                                str(loc.get("locationId")) == target_id
                                for loc in tx.get("location_detail", [])
                            ):
                                payer = p
                                break
                        if payer:
                            break
        except Exception as e:
            print(f"[WARN send_transferku_transaction] Failed to resolve payer for credit_party_identifier: {e}")

    # Build dynamic credit_party_identifier
    credit_party_identifier = build_transferku_credit_party_identifier(
        req=req,
        payer=payer,
        accepted_identifiers=kwargs.get("credit_party_identifiers_accepted"),
        transaction_type=req.transaction_type or kwargs.get("transaction_type") or "C2C",
    )

    # 5. Build body according to Transferku /v1/transfers spec
    body = {
        "quote_id": str(quote_id),
        "external_id": external_id,
        "purpose_of_remittance": req.purpose_of_remittance,
        "source_of_funds": req.sender_source_of_fund,
        "beneficiary_relationship": req.sender_beneficiary_relationship,
        "credit_party_identifier": credit_party_identifier,
        "sender_id": str(sender_id),
        "beneficiary_id": str(beneficiary_id),
        "callback_url": req.callback_url or "https://client.com/webhook",
        "additional_info": req.additional_info or {},
    }
    print(body)

    url = f"{settings.payment_host_transferku}/v1/transfers"
    auth = httpx.BasicAuth(
        settings.payment_client_id_transferku,
        settings.payment_client_secret_transferku,
    )

    async def _post_transfer(c: httpx.AsyncClient) -> dict:
        response = await c.post(url, json=body, auth=auth, timeout=30.0)
        try:
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError:
            err_text = response.text
            print(f"[ERROR send_transferku_transaction] HTTP {response.status_code}: {err_text}")
            try:
                err_json = response.json()
                msg = err_json.get("message") or err_json.get("status_message") or err_text
            except Exception:
                msg = err_text
            raise HTTPException(status_code=response.status_code, detail=f"Transferku error: {msg}")

    if client is not None:
        return await _post_transfer(client)
    else:
        async with httpx.AsyncClient() as new_client:
            return await _post_transfer(new_client)


async def send_lightremit_transaction(
    req: SendTransactionRequest,
    sender: Sender,
    client: Optional[httpx.AsyncClient] = None,
    **kwargs: Any,
) -> dict:
    """
    Sends transaction via LightRemit payment provider.
    """
    url = f"{settings.payment_protocol}{settings.payment_host}{settings.payment_uri}/SendTransaction"
    agent_txn_id = req.agent_txn_id or generate_agent_txn_id()
    agent_session_id = req.agent_session_id or ""
    payload = await build_lightremit_payload(req, sender, agent_session_id, agent_txn_id)
    signature, payload_dict = build_request("POST", url, payload.model_dump(by_alias=True))

    async def _post_lr(c: httpx.AsyncClient) -> dict:
        response = await c.post(url, json=payload_dict, headers={"Authorization": signature}, timeout=30.0)
        try:
            return response.json()
        except ValueError:
            raise HTTPException(status_code=502, detail="Invalid response from payment provider")

    if client is not None:
        return await _post_lr(client)
    else:
        async with httpx.AsyncClient() as new_client:
            return await _post_lr(new_client)


async def send_transaction(
    req: SendTransactionRequest,
    sender: Sender,
    client: Optional[httpx.AsyncClient] = None,
    **kwargs: Any,
) -> tuple[dict, str]:
    """
    Routes and sends transaction to either Transferku or LightRemit
    based on the agent specified in SendTransactionRequest (e.g. 'TRANSFERKU' or 'LIGHTREMIT').
    Returns (response_data, chosen_agent).
    """
    print(req.agent)
    agent = (req.agent or kwargs.get("agent") or "LIGHTREMIT").strip().upper()
    

    if agent == "TRANSFERKU":
        res = await send_transferku_transaction(req, sender=sender, client=client, **kwargs)
        print("res: ", res)
        return res, "TRANSFERKU"
    else:
        res = await send_lightremit_transaction(req, sender=sender, client=client, **kwargs)
        print("res: ", res)
        return res, "LIGHTREMIT"