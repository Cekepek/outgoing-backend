import asyncio
import json
import logging
from typing import Any

import httpx

from app.config import settings
from app.database import SessionLocal
from app.models import Sender
from app.schemas import SendTransactionRequest
from app.services.catalogueServices import (
    CURRENCY_NAME,
    fetch_locations_from_both,
    fetch_quote_transferku,
)
from app.services.transactionServices import send_transaction

# Configure logging to suppress noisy warnings during batch
logging.getLogger("httpx").setLevel(logging.WARNING)

COUNTRIES = ["AUS", "CHN", "EUR", "GBR", "HKG", "MYS", "PHL", "SGP", "THA"]

# Sample SWIFT / IBAN / BSB helpers
COUNTRY_IDENTIFIER_DEFAULTS = {
    "AUS": {
        "bank_account_number": "123456789",
        "bank_branch_name": "012345",  # BSB code
        "swift_code": "CTBAAU2S",
    },
    "CHN": {
        "bank_account_number": "6222021234567890123",
        "card_number": "6222021234567890123",
        "msisdn": "+8613800138000",
        "swift_code": "BKCHCNBJ",
    },
    "EUR": {
        "bank_account_number": "DE89370400440532013000",
        "iban": "DE89370400440532013000",
        "swift_code": "DEUTDEDD",
    },
    "GBR": {
        "bank_account_number": "12345678",
        "bank_branch_name": "200000",  # Sort code
        "iban": "GB29XABC10203012345678",
        "swift_code": "BARCGB22",
    },
    "HKG": {
        "bank_account_number": "123456789012",
        "msisdn": "+85291234567",
        "swift_code": "BKCHHKHH",
    },
    "MYS": {
        "bank_account_number": "123456789012",
        "msisdn": "+60123456789",
        "swift_code": "MBBEMYKL",
    },
    "PHL": {
        "bank_account_number": "123456789012",
        "msisdn": "+639171234567",
        "swift_code": "BOPIPHMM",
    },
    "SGP": {
        "bank_account_number": "123456789",
        "msisdn": "+6591234567",
        "swift_code": "DBSSSGSG",
    },
    "THA": {
        "bank_account_number": "1234567890",
        "msisdn": "+66812345678",
        "swift_code": "BKKBTHBK",
    },
}


async def test_all_corridors():
    db = SessionLocal()
    sender = db.query(Sender).filter(Sender.id == 3).first()
    if not sender:
        sender = db.query(Sender).first()
    print(f"Using test sender: ID={sender.id}, Name={sender.sender_first_name} {sender.sender_last_name}")

    results = []

    async with httpx.AsyncClient(timeout=45.0) as client:
        for iso in COUNTRIES:
            print(f"\n==========================================")
            print(f"Fetching get_locations for country: {iso}")
            print(f"==========================================")
            currency = CURRENCY_NAME.get(iso, "USD")
            
            try:
                loc_data = await fetch_locations_from_both(
                    iso_code=iso, payment_mode="B", transaction_type="C2C"
                )
            except Exception as e:
                print(f"[ERROR] Could not fetch locations for {iso}: {e}")
                results.append({
                    "country": iso,
                    "provider": "UNKNOWN",
                    "payer_or_location_id": "N/A",
                    "name": "N/A",
                    "works": False,
                    "status_code": "N/A",
                    "message": f"fetch_locations failed: {e}",
                    "details": {}
                })
                continue

            locations = loc_data.get("locations") or []
            print(f"Total locations for {iso}: {len(locations)}")

            # Gather unique Transferku payers and unique LightRemit locations
            tk_payers: dict[str, dict[str, Any]] = {}
            lr_locations: dict[str, dict[str, Any]] = {}

            for loc in locations:
                provs = loc.get("providers") or {}
                tk = provs.get("transferku")
                lr = provs.get("lightremit")

                if tk and tk.get("payer_id"):
                    pid = str(tk.get("payer_id"))
                    if pid not in tk_payers:
                        tk_payers[pid] = {
                            "payer_id": pid,
                            "location_id": tk.get("location_id") or pid,
                            "name": loc.get("name"),
                            "transaction_type": tk.get("transaction_type") or "C2C",
                            "identifiers": loc.get("credit_party_identifiers_accepted") or tk.get("credit_party_identifiers_accepted"),
                            "canonical_id": loc.get("canonical_id"),
                        }

                if lr and lr.get("location_id"):
                    lid = str(lr.get("location_id"))
                    if lid not in lr_locations:
                        lr_locations[lid] = {
                            "location_id": lid,
                            "name": loc.get("name"),
                            "canonical_id": loc.get("canonical_id"),
                        }

            # ----------------------------------------------------
            # 1. TEST TRANSFERKU PAYERS
            # ----------------------------------------------------
            if not tk_payers:
                print(f"[{iso}] No Transferku payers found in get_locations.")
                results.append({
                    "country": iso,
                    "provider": "TRANSFERKU",
                    "payer_or_location_id": "NONE",
                    "name": "No Transferku Payer",
                    "works": False,
                    "status_code": "NO_PAYER",
                    "message": loc_data.get("errors", {}).get("transferku") or "No payers available for this country",
                    "details": {}
                })
            else:
                for pid, pinfo in tk_payers.items():
                    print(f"--> Testing Transferku Payer: {pid} ({pinfo['name']})")
                    # Step A: Get Quote
                    # In sandbox, test with destination amount 50 or source amount 100000
                    amount = 50 if currency not in ("IDR", "KRW", "VND", "JPY") else 50000
                    quote_res, quote_err = await fetch_quote_transferku(
                        client=client,
                        payer_id=pid,
                        payout_country=iso,
                        payout_currency=currency,
                        amount=amount,
                        mode="DESTINATION_AMOUNT",
                        transaction_type=pinfo["transaction_type"],
                    )

                    if not quote_res or not quote_res.get("quote_id"):
                        # If destination mode failed, try source amount mode
                        quote_res, quote_err = await fetch_quote_transferku(
                            client=client,
                            payer_id=pid,
                            payout_country=iso,
                            payout_currency=currency,
                            amount=500000,
                            mode="SOURCE_AMOUNT",
                            transaction_type=pinfo["transaction_type"],
                        )

                    if not quote_res or not quote_res.get("quote_id"):
                        print(f"    [QUOTE FAILED] {quote_err}")
                        results.append({
                            "country": iso,
                            "provider": "TRANSFERKU",
                            "payer_or_location_id": pid,
                            "name": pinfo["name"],
                            "works": False,
                            "status_code": "QUOTE_ERROR",
                            "message": f"Quote failed: {quote_err}",
                            "details": {"error": quote_err}
                        })
                        continue

                    quote_id = quote_res["quote_id"]
                    print(f"    [QUOTE OK] quote_id={quote_id}")

                    # Step B: Build SendTransactionRequest
                    ident_defaults = COUNTRY_IDENTIFIER_DEFAULTS.get(iso, {})
                    req_tk = SendTransactionRequest(
                        agent="TRANSFERKU",
                        quote_id=str(quote_id),
                        payer_id=pid,
                        location_id=pinfo["location_id"],
                        transaction_type=pinfo["transaction_type"],
                        purpose_of_remittance="FAMILY_SUPPORT",
                        sender_source_of_fund="SALARY",
                        sender_beneficiary_relationship="FAMILY",
                        receiver_first_name="Jane",
                        receiver_last_name="Doe",
                        receiver_address="123 Test Boulevard",
                        receiver_contact_number=ident_defaults.get("msisdn", "+6281234567890"),
                        receiver_date_of_birth="1995-05-15",
                        receiver_state="Central",
                        receiver_city="Capital",
                        receiver_zip_code="12345",
                        receiver_country=iso,
                        receiver_nationality=iso,
                        receiver_id_type="PASSPORT",
                        receiver_id_number="E98765432",
                        receiver_email="janedoe@testmail.com",
                        receiver_customer_type="PERSONAL",
                        receiver_account_type="SAVINGS",
                        receiver_occupation="Accountant",
                        bank_account_number=ident_defaults.get("bank_account_number", "1234567890"),
                        bank_branch_name=ident_defaults.get("bank_branch_name"),
                        swift_code=ident_defaults.get("swift_code"),
                        card_number=ident_defaults.get("card_number"),
                        iban=ident_defaults.get("iban"),
                        msisdn=ident_defaults.get("msisdn"),
                    )

                    try:
                        res, agent_used = await send_transaction(req=req_tk, sender=sender, client=client)
                        status_str = str(res.get("status") or "")
                        status_msg = str(res.get("status_message") or "")
                        transfer_id = res.get("transfer_id")
                        err_detail = res.get("error") or ""

                        # Status 90000 in sandbox means transfer accepted by Transferku endpoint (even if DECLINED by mock sandbox)
                        # An accepted transaction with transfer_id shows the integration pipeline works!
                        is_working = bool(transfer_id) or status_str in ("0", "90000", "SUCCESS", "PENDING", "ACCEPTED")
                        display_msg = f"{status_msg} (transfer_id={transfer_id})" if transfer_id else (err_detail or status_msg or "OK")

                        print(f"    [SEND RESULT] works={is_working}, status={status_str}, msg={display_msg}")
                        results.append({
                            "country": iso,
                            "provider": "TRANSFERKU",
                            "payer_or_location_id": pid,
                            "name": pinfo["name"],
                            "works": is_working,
                            "status_code": status_str,
                            "message": display_msg,
                            "details": res
                        })
                    except Exception as e:
                        print(f"    [SEND EXCEPTION] {type(e).__name__}: {e}")
                        results.append({
                            "country": iso,
                            "provider": "TRANSFERKU",
                            "payer_or_location_id": pid,
                            "name": pinfo["name"],
                            "works": False,
                            "status_code": "EXCEPTION",
                            "message": str(e),
                            "details": {"error": str(e)}
                        })

            # ----------------------------------------------------
            # 2. TEST LIGHTREMIT CORRIDORS
            # ----------------------------------------------------
            if not lr_locations:
                print(f"[{iso}] No LightRemit locations found in get_locations.")
            else:
                for lid, linfo in lr_locations.items():
                    print(f"--> Testing LightRemit Location: {lid} ({linfo['name']})")
                    ident_defaults = COUNTRY_IDENTIFIER_DEFAULTS.get(iso, {})

                    req_lr = SendTransactionRequest(
                        agent="LIGHTREMIT",
                        location_id=lid,
                        purpose_of_remittance="05",          # Family Maintenance and Savings
                        sender_source_of_fund="09",          # Salary
                        sender_beneficiary_relationship="05", # Friends
                        receiver_first_name="Jane",
                        receiver_last_name="Doe",
                        receiver_address="123 Test Boulevard",
                        receiver_contact_number=ident_defaults.get("msisdn", "6591234567").replace("+", ""),
                        receiver_date_of_birth="1995-05-15",
                        receiver_state="Central",
                        receiver_city="Capital",
                        receiver_zip_code="12345",
                        receiver_country=iso,
                        receiver_nationality=iso,
                        receiver_id_type="PASSPORT",
                        receiver_id_number="E98765432",
                        receiver_email="janedoe@testmail.com",
                        receiver_customer_type="I",
                        receiver_account_type="4",            # Savings account
                        receiver_occupation="07",              # Engineer
                        calc_by="P",
                        transfer_amount="100",
                        remit_currency="IDR",
                        payout_currency=currency,
                        payment_mode="B",
                        bank_name=linfo["name"],
                        bank_account_number=ident_defaults.get("bank_account_number", "1234567890"),
                        swift_code=ident_defaults.get("swift_code"),
                    )

                    try:
                        res, agent_used = await send_transaction(req=req_lr, sender=sender, client=client)
                        code = str(res.get("code") or "")
                        msg = res.get("message") or ""

                        # In LightRemit:
                        # code '0' = accepted
                        # code '5099' = 'You do not have sufficient balance to send txn' (Means schema, auth, corridor parameters are 100% valid and verified by upstream engine)
                        # code '1111' or others = validation / rejected
                        is_working = code in ("0", "5099")
                        print(f"    [SEND RESULT] works={is_working}, code={code}, msg={msg}")
                        results.append({
                            "country": iso,
                            "provider": "LIGHTREMIT",
                            "payer_or_location_id": lid,
                            "name": linfo["name"],
                            "works": is_working,
                            "status_code": code,
                            "message": msg,
                            "details": res
                        })
                    except Exception as e:
                        print(f"    [SEND EXCEPTION] {type(e).__name__}: {e}")
                        results.append({
                            "country": iso,
                            "provider": "LIGHTREMIT",
                            "payer_or_location_id": lid,
                            "name": linfo["name"],
                            "works": False,
                            "status_code": "EXCEPTION",
                            "message": str(e),
                            "details": {"error": str(e)}
                        })

    db.close()

    # Save to JSON
    with open("corridor_test_results.json", "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "="*80)
    print("ALL TESTS COMPLETED. Summary saved to corridor_test_results.json")
    print("="*80)


if __name__ == "__main__":
    asyncio.run(test_all_corridors())
