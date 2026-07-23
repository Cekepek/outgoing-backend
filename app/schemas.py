from typing import Any, Generic, Literal, Optional, TypeVar, Union
from pydantic import BaseModel, ConfigDict, Field,EmailStr, field_validator
from pydantic.generics import GenericModel
from datetime import date


T = TypeVar("T")

class ResponseSchema(GenericModel, Generic[T]):
    status: str
    message: Optional[str] = None
    data: Optional[T] = None
    
class BaseResponse(BaseModel, Generic[T]):
    status: str
    message: str
    data: T

class ErrorItems(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    message: str

class ConnectionBase(BaseModel):
    code: str
    message: str

class CatalogueRequest(BaseModel):
    catalogueType: str = ""
    additionalField1: Optional[str] = ""
    additionalField2: Optional[str] = ""
    additionalField3: Optional[str] = ""

class CatalogueItem(BaseModel):
    data: Optional[str] = None
    value: Optional[str] = None

class CatalogueResponse(BaseModel):
    code: Optional[str] = None
    message: Optional[str] = None
    result: Optional[list[CatalogueItem]] = None

class BankRequest(BaseModel):
    paymentMode: str = ""
    payoutCountry: str = ""

class BankItem(BaseModel):
    locationId: Optional[str] = None
    locationName: Optional[str] = None
    optionalField: Optional[str] = None
    
class RateRequest(BaseModel):
    transfer_amount: str = Field(..., alias="transferAmount")
    calc_by: Literal["C", "P"] = Field(..., alias="calcBy") 
    payout_currency: str = Field(..., alias="payoutCurrency")
    payment_mode: str = Field(..., alias="paymentMode")
    location_id: str = Field(..., alias="locationId")
    payout_country: str = Field(..., alias="payoutCountry")
    class Config:
        populate_by_name = True
        json_schema_extra = {
            "example": {
                "agentSessionId": "{{agentSessionId}}",
                "transferAmount": "100000",
                "calcBy": "C",
                "payoutCurrency": "SGD",
                "paymentMode": "B",
                "locationId": "SGPALL",
                "payoutCountry": "SGP",
            }
        }

class RateItemSuccess(BaseModel):
    collect_amount: str = Field(..., alias="collectAmount")
    collect_currency: str = Field(..., alias="collectCurrency")
    service_charge: str = Field(..., alias="serviceCharge")
    vat_charge: str = Field(..., alias="vatCharge")
    transfer_amount: str = Field(..., alias="transferAmount")
    exchange_rate: str = Field(..., alias="exchangeRate")
    payout_amount: str = Field(..., alias="payoutAmount")
    payout_currency: str = Field(..., alias="payoutCurrency")
    fee_discount: str = Field(..., alias="feeDiscount")
    additional_premium_rate: str = Field(..., alias="additionalPremiumRate")
    settlement_rate: str = Field(..., alias="settlementRate")
    sla_message: str

    class Config:
        populate_by_name = True

RateItem = Union[RateItemSuccess, ErrorItems]

class ExchangeRateItem(BaseModel):
    send_country: str = Field(..., alias="sendCountry")
    send_currency: str = Field(..., alias="sendCurrency")
    send_iso2: str = Field(..., alias="sendISO2")
    receive_country: str = Field(..., alias="receiveCountry")
    receiver_currency: str = Field(..., alias="receiverCurrency")
    receive_country_name: str = Field(..., alias="receiveCountryName")
    receive_iso2: str = Field(..., alias="receiveISO2")
    exchange_rate: str = Field(..., alias="exchangeRate")
    last_modified: str = Field(..., alias="lastModified")

    class Config:
        populate_by_name = True
        
class SendTransactionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    agent_session_id: str = Field(alias="agentSessionId")
    agent_txn_id: str = Field(alias="agentTxnId")
    location_id: str = Field(alias="locationId")

    # --- Sender ---
    sender_first_name: str = Field(alias="senderFirstName")
    sender_middle_name: Optional[str] = Field(default="", alias="senderMiddleName")
    sender_last_name: str = Field(alias="senderLastName")
    sender_gender: str = Field(alias="senderGender")
    sender_address: str = Field(alias="senderAddress")
    sender_city: str = Field(alias="senderCity")
    sender_state: str = Field(alias="senderState")
    sender_zip_code: str = Field(alias="senderZipCode")
    sender_country: str = Field(alias="senderCountry")
    sender_mobile: str = Field(alias="senderMobile")
    sender_nationality: str = Field(alias="senderNationality")
    sender_id_type: str = Field(alias="senderIdType")
    sender_id_number: str = Field(alias="senderIdNumber")
    sender_id_issue_country: str = Field(alias="senderIdIssueCountry")
    sender_id_issue_date: str = Field(alias="senderIdIssueDate")
    sender_id_expire_date: str = Field(alias="senderIdExpireDate")
    sender_date_of_birth: str = Field(alias="senderDateOfBirth")
    sender_occupation: str = Field(alias="senderOccupation")
    sender_source_of_fund: str = Field(alias="senderSourceOfFund")
    sender_secondary_id_type: Optional[str] = Field(default="", alias="senderSecondaryIdType")
    sender_secondary_id_number: Optional[str] = Field(default="", alias="senderSecondaryIdNumber")
    sender_customer_type: str = Field(alias="senderCustomerType")
    sender_email: str = Field(alias="senderEmail")
    sender_native_firstname: Optional[str] = Field(default="", alias="senderNativeFirstname")
    sender_native_lastname: Optional[str] = Field(default="", alias="senderNativeLastname")
    sender_beneficiary_relationship: str = Field(alias="senderBeneficiaryRelationship")
    purpose_of_remittance: str = Field(alias="purposeOfRemittance")

    # --- Sender company (optional, used for corporate senders) ---
    sender_company_name: Optional[str] = Field(default="", alias="senderCompanyName")
    sender_company_reg_number: Optional[str] = Field(default="", alias="senderCompanyRegNumber")
    sender_company_incorporate_date: Optional[str] = Field(default="", alias="senderCompanyIncorporateDate")

    # --- Receiver ---
    receiver_first_name: str = Field(alias="receiverFirstName")
    receiver_middle_name: Optional[str] = Field(default="", alias="receiverMiddleName")
    receiver_last_name: str = Field(alias="receiverLastName")
    receiver_address: str = Field(alias="receiverAddress")
    receiver_contact_number: str = Field(alias="receiverContactNumber")
    receiver_date_of_birth: str = Field(alias="receiverDateOfBirth")
    receiver_state: str = Field(alias="receiverState")
    receiver_area_town: Optional[str] = Field(default="", alias="receiverAreaTown")
    receiver_city: Optional[str] = Field(default="", alias="receiverCity")
    receiver_zip_code: str = Field(alias="receiverZipCode")
    receiver_country: str = Field(alias="receiverCountry")
    receiver_nationality: str = Field(alias="receiverNationality")
    receiver_id_type: str = Field(alias="receiverIdType")
    receiver_id_number: str = Field(alias="receiverIdNumber")
    receiver_email: str = Field(alias="receiverEmail")
    receiver_customer_type: str = Field(alias="receiverCustomerType")
    receiver_native_firstname: Optional[str] = Field(default="", alias="receiverNativeFirstname")
    receiver_native_lastname: Optional[str] = Field(default="", alias="receiverNativeLastname")
    receiver_account_type: str = Field(alias="receiverAccountType")
    receiver_occupation: str = Field(alias="receiverOccupation")
    receiver_wallet_id_number: Optional[str] = Field(default="", alias="receiverWalletIdNumber")

    # --- Receiver company (optional, used for corporate receivers) ---
    receiver_company_name: Optional[str] = Field(default="", alias="receiverCompanyName")
    receiver_company_reg_number: Optional[str] = Field(default="", alias="receiverCompanyRegNumber")
    receiver_company_incorporate_date: Optional[str] = Field(default="", alias="receiverCompanyIncorporateDate")

    # --- Transaction / payout ---
    calc_by: str = Field(alias="calcBy")
    transfer_amount: str = Field(alias="transferAmount")  # monetary -> str
    remit_currency: str = Field(alias="remitCurrency")
    payout_currency: str = Field(alias="payoutCurrency")
    payment_mode: str = Field(alias="paymentMode")
    bank_name: Optional[str] = Field(default="", alias="bankName")
    bank_branch_name: Optional[str] = Field(default="", alias="bankBranchName")
    bank_branch_code: Optional[str] = Field(default="", alias="bankBranchCode")
    bank_account_number: Optional[str] = Field(default="", alias="bankAccountNumber")
    swift_code: Optional[str] = Field(default="", alias="swiftCode")

    # --- Representative (optional, used when a third party acts on behalf of sender/receiver) ---
    representative_name: Optional[str] = Field(default="", alias="RepresentativeName")
    representative_customer_type: Optional[str] = Field(default="", alias="RepresentativeCustomerType")
    representative_id_type: Optional[str] = Field(default="", alias="RepresentativeIdType")
    representative_id_number: Optional[str] = Field(default="", alias="RepresentativeIdNumber")
    representative_date_of_birth: Optional[str] = Field(default="", alias="RepresentativeDateOfBirth")
    representative_nationality: Optional[str] = Field(default="", alias="RepresentativeNationality")
    representative_address: Optional[str] = Field(default="", alias="RepresentativeAddress")
    representative_contact_number: Optional[str] = Field(default="", alias="RepresentativeContactNumber")

    dynamic_fields: list[Any] = Field(default_factory=list, alias="dynamicFields")


    
class SendTransactionResponseSuccess(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    
    confirmation_id: str = Field(alias="confirmationId")
    agent_txn_id: str = Field(alias="agentTxnId")

    collect_amount: str = Field(alias="collectAmount")       # monetary -> str
    collect_currency: str = Field(alias="collectCurrency")
    service_charge: str = Field(alias="serviceCharge")       # monetary -> str
    gst_charge: str = Field(default="", alias="gstCharge")   # monetary -> str, can be empty

    transfer_amount: str = Field(alias="transferAmount")     # monetary -> str
    exchange_rate: str = Field(alias="exchangeRate")         # monetary -> str

    payout_amount: str = Field(alias="payoutAmount")         # monetary -> str
    payout_currency: str = Field(alias="payoutCurrency")

    fee_discount: str = Field(alias="feeDiscount")           # monetary -> str
    additional_premium_rate: str = Field(alias="additionalPremiumRate")  # monetary -> str

    txn_date: str = Field(alias="txnDate")

    settlement_rate: str = Field(alias="settlementRate")     # monetary -> str
    send_commission: str = Field(alias="sendCommission")     # monetary -> str
    settlement_amount: str = Field(alias="settlementAmount") # monetary -> str

SendTransactionResponse = Union[SendTransactionResponseSuccess, ErrorItems]

class LoginSchema(BaseModel):
    username: str
    password: str 



class RegisterSchema(BaseModel):
    # --- User account fields ---
    username: str
    password: str
    pin: str

    # --- Sender identity ---
    sender_customer_type: str  # "B" | "I"
    sender_first_name: Optional[str] = None
    sender_middle_name: Optional[str] = None
    sender_last_name: Optional[str] = None
    sender_company_name: Optional[str] = None
    sender_company_reg_number: Optional[str] = None
    sender_company_incorporate_date: Optional[date] = None
    sender_gender: Optional[str] = None
    sender_native_first_name: Optional[str] = None
    sender_native_last_name: Optional[str] = None

    # --- Contact & Address ---
    sender_address: Optional[str] = None
    sender_city: Optional[str] = None
    sender_state: Optional[str] = None
    sender_zip_code: Optional[str] = None
    sender_country: str  # ISO-3, required
    sender_mobile: Optional[str] = None
    sender_email: Optional[EmailStr] = None
    sender_nationality: Optional[str] = None

    # --- ID Document ---
    sender_id_type: str
    sender_id_number: str
    sender_id_issue_country: Optional[str] = None
    sender_id_issue_date: Optional[date] = None
    sender_id_expire_date: Optional[date] = None
    sender_date_of_birth: Optional[date] = None
    sender_secondary_id_type: Optional[str] = None
    sender_secondary_id_number: Optional[str] = None

    # --- Transaction Info ---
    sender_occupation: Optional[str] = None
    sender_source_of_fund: Optional[str] = None

    # --- Validation ---
    @field_validator("username")
    @classmethod
    def username_length(cls, v: str) -> str:
        if len(v) < 4:
            raise ValueError("Username minimal 6 karakter")
        return v

    @field_validator("password")
    @classmethod
    def password_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password minimal 8 karakter")
        return v

    @field_validator("pin")
    @classmethod
    def pin_format(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 6:
            raise ValueError("PIN harus 6 digit angka")
        return v

    @field_validator("sender_customer_type")
    @classmethod
    def customer_type_valid(cls, v: str) -> str:
        if v not in ("B", "I"):
            raise ValueError("sender_customer_type harus 'B' atau 'I'")
        return v
