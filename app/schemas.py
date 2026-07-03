from typing import Generic, Literal, Optional, TypeVar
from pydantic import BaseModel, Field
from pydantic.generics import GenericModel


T = TypeVar("T")

class ResponseSchema(GenericModel, Generic[T]):
    status: str
    message: Optional[str] = None
    data: Optional[T] = None
    
class BaseResponse(BaseModel, Generic[T]):
    status: str
    message: str
    data: T

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
    agentSessionId: Optional[str] = None
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

class RateItem(BaseModel):
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