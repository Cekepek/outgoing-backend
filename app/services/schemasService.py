from datetime import date
from app.models import Sender
from app.schemas import SendTransactionRequest
from app.schemas import LightRemitSendTransactionPayload
def _fmt_date(d: date | None) -> str:
    return d.strftime("%Y-%m-%d") if d else ""

async def build_lightremit_payload(
    req: SendTransactionRequest,
    sender: Sender,
    agent_session_id: str, agent_txn_id: str
) -> LightRemitSendTransactionPayload:
    sender_fields = dict(
        sender_customer_type=sender.sender_customer_type,
        sender_country=sender.sender_country,
        sender_id_type=sender.sender_id_type,
        sender_id_number=sender.sender_id_number,
        sender_first_name=sender.sender_first_name or "",
        sender_middle_name=sender.sender_middle_name or "",
        sender_last_name=sender.sender_last_name or "",
        sender_gender=sender.sender_gender or "",
        sender_address=sender.sender_address or "",
        sender_city=sender.sender_city or "",
        sender_state=sender.sender_state or "",
        sender_zip_code=sender.sender_zip_code or "",
        sender_mobile=sender.sender_mobile or "",
        sender_email=sender.sender_email or "",
        sender_nationality=sender.sender_nationality or "",
        sender_id_issue_country=sender.sender_id_issue_country or "",
        sender_id_issue_date=_fmt_date(sender.sender_id_issue_date),
        sender_id_expire_date=_fmt_date(sender.sender_id_expire_date),
        sender_date_of_birth=_fmt_date(sender.sender_date_of_birth),
        sender_secondary_id_type=sender.sender_secondary_id_type or "",
        sender_secondary_id_number=sender.sender_secondary_id_number or "",
        sender_occupation=sender.sender_occupation or "",
        sender_native_first_name=sender.sender_native_first_name or "",
        sender_native_last_name=sender.sender_native_last_name or "",
    )

    # Business senders don't have first/last name — company fields take over
    if sender.sender_customer_type == "B":
        sender_fields.update(
            sender_company_name=sender.sender_company_name or "",
            sender_company_reg_number=sender.sender_company_reg_number or "",
            sender_company_incorporate_date=_fmt_date(sender.sender_company_incorporate_date),
        )
    else:
        sender_fields.update(
            sender_company_name="",
            sender_company_reg_number="",
            sender_company_incorporate_date="",
        )

    return LightRemitSendTransactionPayload(
        agent_session_id=agent_session_id,
        agent_txn_id=agent_txn_id,
        location_id=req.location_id,
        purpose_of_remittance=req.purpose_of_remittance,
        sender_beneficiary_relationship=req.sender_beneficiary_relationship,
        sender_source_of_fund=req.sender_source_of_fund,
        **sender_fields,
        **req.model_dump(by_alias=False, include={
            # all receiver_* field names
            "receiver_first_name", "receiver_middle_name", "receiver_last_name",
            "receiver_address", "receiver_contact_number", "receiver_date_of_birth",
            "receiver_state", "receiver_area_town", "receiver_city", "receiver_zip_code",
            "receiver_country", "receiver_nationality", "receiver_id_type", "receiver_id_number",
            "receiver_email", "receiver_customer_type", "receiver_native_firstname",
            "receiver_native_lastname", "receiver_account_type", "receiver_occupation",
            "receiver_wallet_id_number", "receiver_company_name", "receiver_company_reg_number",
            "receiver_company_incorporate_date",
            # transaction/payout
            "calc_by", "transfer_amount", "remit_currency", "payout_currency", "payment_mode",
            "bank_name", "bank_branch_name", "bank_branch_code", "bank_account_number", "swift_code",
            # representative
            "representative_name", "representative_customer_type", "representative_id_type",
            "representative_id_number", "representative_date_of_birth", "representative_nationality",
            "representative_address", "representative_contact_number",
        }),
        dynamic_fields=req.dynamic_fields,
    )