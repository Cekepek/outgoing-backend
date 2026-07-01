from typing import Generic, Optional, TypeVar
from pydantic import BaseModel
from pydantic.generics import GenericModel


T = TypeVar("T")

class ResponseSchema(GenericModel, Generic[T]):
    status: str
    message: Optional[str] = None
    data: Optional[T] = None

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

