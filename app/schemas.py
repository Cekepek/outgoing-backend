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

