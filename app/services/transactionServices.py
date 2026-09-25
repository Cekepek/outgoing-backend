import os
import random
import string
import time
import uuid
from datetime import date, datetime
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
    TransferkuCustomerResponse,
)
from app.services.schemasService import build_lightremit_payload
from app.utils.redis import redis_client
from app.utils.signature import build_request, generate_agent_txn_id

# In-memory fallback cache for customer_id
CUSTOMER_ID_CACHE: dict[str, str] = {}


def generate_uuidv7() -> str:
    """
    Generates a UUIDv7 string according to RFC 9562:
    - 48 bits: Unix timestamp in milliseconds
    - 4 bits: Version 7 (0b0111)
    - 12 bits: Random sequence
    - 2 bits: Variant RFC 4122 (0b10)
    - 62 bits: Random sequence
    """
    ms = int(time.time() * 1000)
    rand_bytes = bytearray(os.urandom(16))
    rand_bytes[0:6] = ms.to_bytes(6, byteorder="big")
    rand_bytes[6] = (rand_bytes[6] & 0x0F) | 0x70
    rand_bytes[8] = (rand_bytes[8] & 0x3F) | 0x80
    return str(uuid.UUID(bytes=bytes(rand_bytes)))


def generate_transferku_external_id() -> str:
    """
    Generates a unique 24-digit external ID (timestamp YYYYMMDDHHMMSS + 10 random digits).
    Example: '202512180000002841111254'
    """
    return datetime.now().strftime("%Y%m%d%H%M%S") + "".join(random.choices(string.digits, k=10))


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


async def save_beneficiary_customer_id(
    customer_id: str,
    id_number: Optional[str] = None,
    external_id: Optional[str] = None,
    user_id: Optional[int] = None,
    registration_number: Optional[str] = None,
) -> None:
    """
    Saves customer_id for a beneficiary in both in-memory cache and Redis.
    """
    if id_number is not None:
        CUSTOMER_ID_CACHE[f"beneficiary:id_number:{id_number}"] = customer_id
    if registration_number is not None:
        CUSTOMER_ID_CACHE[f"beneficiary:registration_number:{registration_number}"] = customer_id
        CUSTOMER_ID_CACHE[f"beneficiary:id_number:{registration_number}"] = customer_id
    if external_id is not None:
        CUSTOMER_ID_CACHE[f"beneficiary:external_id:{external_id}"] = customer_id
    if user_id is not None:
        CUSTOMER_ID_CACHE[f"beneficiary:user:{user_id}"] = customer_id
    CUSTOMER_ID_CACHE["latest_beneficiary"] = customer_id

    try:
        ttl = getattr(settings, "redis_cache_ttl", 86400 * 7)
        if id_number is not None:
            await redis_client.set(f"transferku:beneficiary:id_number:{id_number}", customer_id, ex=ttl)
        if registration_number is not None:
            await redis_client.set(f"transferku:beneficiary:registration_number:{registration_number}", customer_id, ex=ttl)
            await redis_client.set(f"transferku:beneficiary:id_number:{registration_number}", customer_id, ex=ttl)
        if external_id is not None:
            await redis_client.set(f"transferku:beneficiary:external_id:{external_id}", customer_id, ex=ttl)
        if user_id is not None:
            await redis_client.set(f"transferku:beneficiary:user:{user_id}", customer_id, ex=ttl)
    except Exception as e:
        print(f"[WARN save_beneficiary_customer_id] Redis caching failed: {e}")


async def get_beneficiary_customer_id(
    id_number: Optional[str] = None,
    external_id: Optional[str] = None,
    user_id: Optional[int] = None,
    registration_number: Optional[str] = None,
) -> Optional[str]:
    """
    Retrieves the stored customer_id for a beneficiary, checking in-memory cache first, then Redis.
    """
    lookup_id = id_number or registration_number
    if lookup_id is not None and f"beneficiary:id_number:{lookup_id}" in CUSTOMER_ID_CACHE:
        return CUSTOMER_ID_CACHE[f"beneficiary:id_number:{lookup_id}"]
    if registration_number is not None and f"beneficiary:registration_number:{registration_number}" in CUSTOMER_ID_CACHE:
        return CUSTOMER_ID_CACHE[f"beneficiary:registration_number:{registration_number}"]
    if external_id is not None and f"beneficiary:external_id:{external_id}" in CUSTOMER_ID_CACHE:
        return CUSTOMER_ID_CACHE[f"beneficiary:external_id:{external_id}"]
    if user_id is not None and f"beneficiary:user:{user_id}" in CUSTOMER_ID_CACHE:
        return CUSTOMER_ID_CACHE[f"beneficiary:user:{user_id}"]

    try:
        if lookup_id is not None:
            cid = await redis_client.get(f"transferku:beneficiary:id_number:{lookup_id}")
            if cid:
                CUSTOMER_ID_CACHE[f"beneficiary:id_number:{lookup_id}"] = cid
                return cid
        if registration_number is not None:
            cid = await redis_client.get(f"transferku:beneficiary:registration_number:{registration_number}")
            if cid:
                CUSTOMER_ID_CACHE[f"beneficiary:registration_number:{registration_number}"] = cid
                return cid
        if external_id is not None:
            cid = await redis_client.get(f"transferku:beneficiary:external_id:{external_id}")
            if cid:
                CUSTOMER_ID_CACHE[f"beneficiary:external_id:{external_id}"] = cid
                return cid
        if user_id is not None:
            cid = await redis_client.get(f"transferku:beneficiary:user:{user_id}")
            if cid:
                CUSTOMER_ID_CACHE[f"beneficiary:user:{user_id}"] = cid
                return cid
    except Exception as e:
        print(f"[WARN get_beneficiary_customer_id] Redis retrieval failed: {e}")

    return CUSTOMER_ID_CACHE.get("latest_beneficiary")


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

    external_id = kwargs.get("external_id") or generate_transferku_external_id()
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    if sender is not None:
        sender_id_val = sender.id
        user_id_val = sender.user_id
        customer_type = "BUSINESS" if (sender.sender_customer_type or "").upper() in ("B", "BUSINESS") else "PERSONAL"

        if customer_type == "BUSINESS":
            registered_name = (
                kwargs.get("registered_name")
                or sender.sender_company_name
                or f"{sender.sender_first_name or ''} {sender.sender_last_name or ''}".strip()
            )
            trading_name = (
                kwargs.get("trading_name")
                or getattr(sender, "sender_trading_name", None)
                or sender.sender_company_name
                or registered_name
            )

            body = {
                "external_id": external_id,
                "role": "SENDER",
                "customer_type": "BUSINESS",
                "registered_name": registered_name,
                "trading_name": trading_name,
                "registration_number": (
                    kwargs.get("registration_number")
                    or sender.sender_company_reg_number
                    or sender.sender_id_number
                    or ""
                ),
                "country_iso_code": kwargs.get("country_iso_code") or sender.sender_country or "IDN",
                "address": kwargs.get("address") or sender.sender_address or "",
                "city": kwargs.get("city") or sender.sender_city or "",
                "postal_code": kwargs.get("postal_code") or sender.sender_zip_code or "",
                "msisdn": kwargs.get("msisdn") or sender.sender_mobile or "",
                "email": kwargs.get("email") or sender.sender_email or "",
                "representative_firstname": kwargs.get("representative_firstname") or sender.sender_first_name or "",
                "representative_lastname": kwargs.get("representative_lastname") or sender.sender_last_name or "",
                "business_relationship": (
                    kwargs.get("business_relationship")
                    or kwargs.get("sender_beneficiary_relationship")
                    or "BUSINESS_PARTNER"
                ),
            }
        else:
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
                "id_type": sender.sender_id_type or "NATIONAL_ID",
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
    elif payload is not None:
        sender_id_val = sender_id or kwargs.get("sender_id")
        user_id_val = user_id or kwargs.get("user_id")
        p_dict = payload.model_dump() if isinstance(payload, BaseModel) else dict(payload)
        raw_cust_type = (
            p_dict.get("customer_type")
            or p_dict.get("sender_customer_type")
            or kwargs.get("customer_type")
            or "PERSONAL"
        )
        is_business = str(raw_cust_type).upper() in ("B", "BUSINESS")

        if is_business:
            registered_name = (
                p_dict.get("registered_name")
                or p_dict.get("sender_company_name")
                or f"{p_dict.get('firstname', '')} {p_dict.get('lastname', '')}".strip()
                or kwargs.get("registered_name", "")
            )
            trading_name = (
                p_dict.get("trading_name")
                or p_dict.get("registered_name")
                or p_dict.get("sender_company_name")
                or registered_name
            )
            body = {
                "external_id": p_dict.get("external_id") or external_id,
                "role": p_dict.get("role", "SENDER"),
                "customer_type": "BUSINESS",
                "registered_name": registered_name,
                "trading_name": trading_name,
                "registration_number": (
                    p_dict.get("registration_number")
                    or p_dict.get("sender_company_reg_number")
                    or p_dict.get("id_number", "")
                ),
                "country_iso_code": p_dict.get("country_iso_code") or p_dict.get("sender_country", "IDN"),
                "address": p_dict.get("address") or p_dict.get("sender_address", ""),
                "city": p_dict.get("city") or p_dict.get("sender_city", ""),
                "postal_code": p_dict.get("postal_code") or p_dict.get("sender_zip_code", ""),
                "msisdn": p_dict.get("msisdn") or p_dict.get("sender_mobile", ""),
                "email": p_dict.get("email") or p_dict.get("sender_email", ""),
                "representative_firstname": (
                    p_dict.get("representative_firstname")
                    or p_dict.get("firstname")
                    or p_dict.get("sender_first_name", "")
                ),
                "representative_lastname": (
                    p_dict.get("representative_lastname")
                    or p_dict.get("lastname")
                    or p_dict.get("sender_last_name", "")
                ),
                "business_relationship": (
                    p_dict.get("business_relationship")
                    or kwargs.get("business_relationship", "BUSINESS_PARTNER")
                ),
            }
        else:
            body = p_dict
            body["external_id"] = external_id
            body.setdefault("role", "SENDER")
            body.setdefault("customer_type", "PERSONAL")
    elif str(kwargs.get("customer_type") or kwargs.get("sender_customer_type") or "").upper() in ("B", "BUSINESS"):
        sender_id_val = sender_id or kwargs.get("sender_id")
        user_id_val = user_id or kwargs.get("user_id")
        registered_name = (
            kwargs.get("registered_name")
            or kwargs.get("sender_company_name")
            or f"{kwargs.get('firstname', '')} {kwargs.get('lastname', '')}".strip()
        )
        trading_name = kwargs.get("trading_name") or registered_name
        body = {
            "external_id": kwargs.get("external_id") or external_id,
            "role": kwargs.get("role", "SENDER"),
            "customer_type": "BUSINESS",
            "registered_name": registered_name,
            "trading_name": trading_name,
            "registration_number": (
                kwargs.get("registration_number")
                or kwargs.get("sender_company_reg_number")
                or kwargs.get("id_number", "")
            ),
            "country_iso_code": kwargs.get("country_iso_code", "IDN"),
            "address": kwargs.get("address", ""),
            "city": kwargs.get("city", ""),
            "postal_code": kwargs.get("postal_code", ""),
            "msisdn": kwargs.get("msisdn", ""),
            "email": kwargs.get("email", ""),
            "representative_firstname": kwargs.get("representative_firstname") or kwargs.get("firstname", ""),
            "representative_lastname": kwargs.get("representative_lastname") or kwargs.get("lastname", ""),
            "business_relationship": kwargs.get("business_relationship", "BUSINESS_PARTNER"),
        }
    else:
        raise HTTPException(status_code=400, detail="Sender information could not be retrieved")

    for k, v in kwargs.items():
        if k in body and k not in ("external_id", "role"):
            body[k] = v

    url = f"{settings.payment_host_transferku}/v1/customers"
    auth = httpx.BasicAuth(
        settings.payment_client_id_transferku,
        settings.payment_client_secret_transferku,
    )

    async def _post_customer(c: httpx.AsyncClient) -> dict:
        response = await c.post(url, json=body, auth=auth, timeout=30.0)
        try:
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError:
            err_text = response.text
            print(f"[ERROR create_sender] HTTP {response.status_code}: {err_text}")
            try:
                err_json = response.json()
                msg = err_json.get("message") or err_json.get("status_message") or err_text
            except Exception:
                msg = err_text
            raise HTTPException(status_code=response.status_code, detail=f"Transferku error: {msg}")

    if client is not None:
        data = await _post_customer(client)
    else:
        async with httpx.AsyncClient() as new_client:
            data = await _post_customer(new_client)

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
    now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    if isinstance(req, SendTransactionRequest):
        is_business = (req.receiver_customer_type or "").upper() in ("B", "BUSINESS")
        if is_business:
            registered_name = (
                kwargs.get("registered_name")
                or getattr(req, "receiver_company_name", None)
                or f"{req.receiver_first_name or ''} {req.receiver_last_name or ''}".strip()
            )
            trading_name = (
                kwargs.get("trading_name")
                or getattr(req, "receiver_company_name", None)
                or registered_name
            )
            registration_number = (
                kwargs.get("registration_number")
                or getattr(req, "receiver_company_reg_number", None)
                or req.receiver_id_number
                or ""
            )

            rep_first = kwargs.get("representative_firstname") or ""
            rep_last = kwargs.get("representative_lastname") or ""
            if not rep_first and not rep_last:
                rep_name = getattr(req, "representative_name", None)
                if rep_name:
                    parts = rep_name.strip().split(None, 1)
                    rep_first = parts[0]
                    rep_last = parts[1] if len(parts) > 1 else ""
                else:
                    rep_first = req.receiver_first_name or ""
                    rep_last = req.receiver_last_name or ""

            body = {
                "external_id": external_id,
                "role": "BENEFICIARY",
                "customer_type": "BUSINESS",
                "registered_name": registered_name,
                "trading_name": trading_name,
                "registration_number": registration_number,
                "country_iso_code": req.receiver_country or kwargs.get("country_iso_code", "CHN"),
                "address": req.receiver_address or kwargs.get("address", ""),
                "city": req.receiver_city or req.receiver_area_town or kwargs.get("city", ""),
                "postal_code": req.receiver_zip_code or kwargs.get("postal_code", ""),
                "msisdn": req.receiver_contact_number or kwargs.get("msisdn", ""),
                "email": req.receiver_email or kwargs.get("email", ""),
                "representative_firstname": rep_first,
                "representative_lastname": rep_last,
                "business_relationship": (
                    kwargs.get("business_relationship")
                    or getattr(req, "sender_beneficiary_relationship", None)
                    or "BUSINESS_PARTNER"
                ),
            }
        else:
            body = {
                "external_id": external_id,
                "role": "BENEFICIARY",
                "customer_type": "PERSONAL",
                "firstname": req.receiver_first_name or "",
                "lastname": req.receiver_last_name or "",
                "gender": _map_gender(kwargs.get("gender") or getattr(req, "receiver_gender", None) or "MALE"),
                "date_of_birth": _format_date(req.receiver_date_of_birth),
                "nationality_country_iso_code": req.receiver_nationality or req.receiver_country or "IDN",
                "id_type": req.receiver_id_type or "NATIONAL_ID",
                "id_number": req.receiver_id_number or "",
                "id_country_iso_code": getattr(req, "receiver_id_issue_country", None) or req.receiver_country or "AUS",
                "address": req.receiver_address or "",
                "city": req.receiver_city or req.receiver_area_town or "",
                "province_state": req.receiver_state or "",
                "postal_code": req.receiver_zip_code or "",
                "country_iso_code": req.receiver_country or "AUS",
                "msisdn": req.receiver_contact_number or "",
                "email": req.receiver_email or "",
                "occupation": req.receiver_occupation or "Civil Servant",
                "source_of_funds": getattr(req, "sender_source_of_fund", None) or kwargs.get("source_of_funds", "SALARY"),
                "created_at": now_iso,
                "updated_at": now_iso,
            }
    elif isinstance(req, (dict, BaseModel)):
        req_dict = req.model_dump() if isinstance(req, BaseModel) else req
        raw_cust_type = (
            req_dict.get("customer_type")
            or req_dict.get("receiver_customer_type")
            or kwargs.get("customer_type")
            or "PERSONAL"
        )
        is_business = str(raw_cust_type).upper() in ("B", "BUSINESS")

        if is_business:
            registered_name = (
                req_dict.get("registered_name")
                or req_dict.get("receiver_company_name")
                or f"{req_dict.get('receiver_first_name', '')} {req_dict.get('receiver_last_name', '')}".strip()
                or req_dict.get("name", "")
                or kwargs.get("registered_name", "")
            )
            trading_name = (
                req_dict.get("trading_name")
                or req_dict.get("receiver_company_name")
                or registered_name
            )
            registration_number = (
                req_dict.get("registration_number")
                or req_dict.get("receiver_company_reg_number")
                or req_dict.get("receiver_id_number")
                or req_dict.get("id_number", "")
                or kwargs.get("registration_number", "")
            )

            rep_first = req_dict.get("representative_firstname") or kwargs.get("representative_firstname") or ""
            rep_last = req_dict.get("representative_lastname") or kwargs.get("representative_lastname") or ""
            if not rep_first and not rep_last:
                rep_name = req_dict.get("representative_name") or ""
                if rep_name:
                    parts = rep_name.strip().split(None, 1)
                    rep_first = parts[0]
                    rep_last = parts[1] if len(parts) > 1 else ""
                else:
                    rep_first = req_dict.get("receiver_first_name") or req_dict.get("firstname") or ""
                    rep_last = req_dict.get("receiver_last_name") or req_dict.get("lastname") or ""

            body = {
                "external_id": req_dict.get("external_id") or external_id,
                "role": req_dict.get("role", "BENEFICIARY"),
                "customer_type": "BUSINESS",
                "registered_name": registered_name,
                "trading_name": trading_name,
                "registration_number": registration_number,
                "country_iso_code": req_dict.get("country_iso_code") or req_dict.get("receiver_country") or "CHN",
                "address": req_dict.get("address") or req_dict.get("receiver_address", ""),
                "city": req_dict.get("city") or req_dict.get("receiver_city") or req_dict.get("receiver_area_town", ""),
                "postal_code": req_dict.get("postal_code") or req_dict.get("receiver_zip_code", ""),
                "msisdn": req_dict.get("msisdn") or req_dict.get("receiver_contact_number", ""),
                "email": req_dict.get("email") or req_dict.get("receiver_email", ""),
                "representative_firstname": rep_first,
                "representative_lastname": rep_last,
                "business_relationship": (
                    req_dict.get("business_relationship")
                    or req_dict.get("sender_beneficiary_relationship")
                    or kwargs.get("business_relationship", "BUSINESS_PARTNER")
                ),
            }
        else:
            body = {
                "external_id": external_id,
                "role": "BENEFICIARY",
                "customer_type": "PERSONAL",
                "firstname": req_dict.get("firstname") or req_dict.get("receiver_first_name", ""),
                "lastname": req_dict.get("lastname") or req_dict.get("receiver_last_name", ""),
                "gender": _map_gender(req_dict.get("gender") or req_dict.get("receiver_gender", "MALE")),
                "date_of_birth": _format_date(req_dict.get("date_of_birth") or req_dict.get("receiver_date_of_birth", "")),
                "nationality_country_iso_code": req_dict.get("nationality_country_iso_code") or req_dict.get("receiver_nationality", "IDN"),
                "id_type": req_dict.get("id_type") or req_dict.get("receiver_id_type", "NATIONAL_ID"),
                "id_number": req_dict.get("id_number") or req_dict.get("receiver_id_number", ""),
                "id_country_iso_code": req_dict.get("id_country_iso_code") or req_dict.get("receiver_id_issue_country") or req_dict.get("receiver_country", "AUS"),
                "address": req_dict.get("address") or req_dict.get("receiver_address", ""),
                "city": req_dict.get("city") or req_dict.get("receiver_city") or req_dict.get("receiver_area_town", ""),
                "province_state": req_dict.get("province_state") or req_dict.get("receiver_state", ""),
                "postal_code": req_dict.get("postal_code") or req_dict.get("receiver_zip_code", ""),
                "country_iso_code": req_dict.get("country_iso_code") or req_dict.get("receiver_country", "AUS"),
                "msisdn": req_dict.get("msisdn") or req_dict.get("receiver_contact_number", ""),
                "email": req_dict.get("email") or req_dict.get("receiver_email", ""),
                "occupation": req_dict.get("occupation") or req_dict.get("receiver_occupation", "Civil Servant"),
                "source_of_funds": req_dict.get("source_of_funds") or req_dict.get("sender_source_of_fund", "SALARY"),
                "created_at": req_dict.get("created_at", now_iso),
                "updated_at": req_dict.get("updated_at", now_iso),
            }
    else:
        raw_cust_type = kwargs.get("customer_type") or kwargs.get("receiver_customer_type", "PERSONAL")
        is_business = str(raw_cust_type).upper() in ("B", "BUSINESS")

        if is_business:
            registered_name = (
                kwargs.get("registered_name")
                or kwargs.get("receiver_company_name")
                or f"{kwargs.get('firstname', '')} {kwargs.get('lastname', '')}".strip()
            )
            trading_name = kwargs.get("trading_name") or registered_name
            body = {
                "external_id": kwargs.get("external_id") or external_id,
                "role": kwargs.get("role", "BENEFICIARY"),
                "customer_type": "BUSINESS",
                "registered_name": registered_name,
                "trading_name": trading_name,
                "registration_number": (
                    kwargs.get("registration_number")
                    or kwargs.get("receiver_company_reg_number")
                    or kwargs.get("id_number", "")
                ),
                "country_iso_code": kwargs.get("country_iso_code", "CHN"),
                "address": kwargs.get("address", ""),
                "city": kwargs.get("city", ""),
                "postal_code": kwargs.get("postal_code", ""),
                "msisdn": kwargs.get("msisdn", ""),
                "email": kwargs.get("email", ""),
                "representative_firstname": kwargs.get("representative_firstname") or kwargs.get("firstname", ""),
                "representative_lastname": kwargs.get("representative_lastname") or kwargs.get("lastname", ""),
                "business_relationship": kwargs.get("business_relationship", "BUSINESS_PARTNER"),
            }
        else:
            body = {
                "external_id": external_id,
                "role": "BENEFICIARY",
                "customer_type": "PERSONAL",
                "firstname": kwargs.get("firstname", ""),
                "lastname": kwargs.get("lastname", ""),
                "gender": _map_gender(kwargs.get("gender", "MALE")),
                "date_of_birth": _format_date(kwargs.get("date_of_birth", "")),
                "nationality_country_iso_code": kwargs.get("nationality_country_iso_code", "IDN"),
                "id_type": kwargs.get("id_type", "NATIONAL_ID"),
                "id_number": kwargs.get("id_number", ""),
                "id_country_iso_code": kwargs.get("id_country_iso_code", "AUS"),
                "address": kwargs.get("address", ""),
                "city": kwargs.get("city", ""),
                "province_state": kwargs.get("province_state", ""),
                "postal_code": kwargs.get("postal_code", ""),
                "country_iso_code": kwargs.get("country_iso_code", "AUS"),
                "msisdn": kwargs.get("msisdn", ""),
                "email": kwargs.get("email", ""),
                "occupation": kwargs.get("occupation", "Civil Servant"),
                "source_of_funds": kwargs.get("source_of_funds", "SALARY"),
                "created_at": now_iso,
                "updated_at": now_iso,
            }

    # Allow explicit overrides
    for k, v in kwargs.items():
        if k in body and k not in ("external_id", "role"):
            body[k] = v

    url = f"{settings.payment_host_transferku}/v1/customers"
    auth = httpx.BasicAuth(
        settings.payment_client_id_transferku,
        settings.payment_client_secret_transferku,
    )

    async def _post_beneficiary(c: httpx.AsyncClient) -> dict:
        response = await c.post(url, json=body, auth=auth, timeout=30.0)
        try:
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError:
            err_text = response.text
            print(f"[ERROR create_beneficiary] HTTP {response.status_code}: {err_text}")
            try:
                err_json = response.json()
                msg = err_json.get("message") or err_json.get("status_message") or err_text
            except Exception:
                msg = err_text
            raise HTTPException(status_code=response.status_code, detail=f"Transferku error: {msg}")

    if client is not None:
        data = await _post_beneficiary(client)
    else:
        async with httpx.AsyncClient() as new_client:
            data = await _post_beneficiary(new_client)

    customer_id = data.get("customer_id") or body.get("customer_id")
    if customer_id:
        id_number = body.get("id_number")
        registration_number = body.get("registration_number")
        await save_beneficiary_customer_id(
            customer_id=customer_id,
            id_number=id_number or registration_number,
            external_id=external_id,
            user_id=user_id or kwargs.get("user_id"),
            registration_number=registration_number,
        )
        data.setdefault("customer_id", customer_id)

    return data


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
    if not quote_id and req.quote:
        if isinstance(req.quote, dict):
            quote_id = req.quote.get("quote_id") or req.quote.get("id")
        elif isinstance(req.quote, str):
            quote_id = req.quote
    if not quote_id:
        quote_id = kwargs.get("quote_id")
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
    if not beneficiary_id:
        beneficiary_id = await get_beneficiary_customer_id(
            id_number=req.receiver_id_number or getattr(req, "receiver_company_reg_number", None),
            user_id=sender.user_id,
            registration_number=getattr(req, "receiver_company_reg_number", None),
        )
    if not beneficiary_id:
        bene_res = await create_beneficiary(
            req=req,
            client=client,
            user_id=sender.user_id,
        )
        beneficiary_id = bene_res.get("customer_id")

    # 4. Generate unique external_id
    external_id = generate_transferku_external_id()

    # 5. Build body according to Transferku /v1/transfers spec
    body = {
        "quote_id": str(quote_id),
        "external_id": external_id,
        "purpose_of_remittance": req.purpose_of_remittance,
        "source_of_funds": req.sender_source_of_fund,
        "beneficiary_relationship": req.sender_beneficiary_relationship,
        "credit_party_identifier": {
            "bank_account_number": req.bank_account_number or "",
            "bank_branch_name": req.bank_branch_name or req.bank_branch_code or "",
            "location_detail": {
                "locationId": req.location_id,
                "locationName": req.location_name or req.bank_name or "",
            },
        },
        "sender_id": str(sender_id),
        "beneficiary_id": str(beneficiary_id),
        "callback_url": req.callback_url or "https://client.com/webhook",
        "additional_info": req.additional_info or {},
    }

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
    agent = (req.agent or kwargs.get("agent") or "LIGHTREMIT").strip().upper()

    if agent == "TRANSFERKU":
        res = await send_transferku_transaction(req, sender=sender, client=client, **kwargs)
        return res, "TRANSFERKU"
    else:
        res = await send_lightremit_transaction(req, sender=sender, client=client, **kwargs)
        return res, "LIGHTREMIT"


def get_transaction_status():
    pass


def get_transaction_history():
    pass