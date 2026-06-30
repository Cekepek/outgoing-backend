from fastapi import APIRouter

from app.schemas import ConnectionBase, ResponseSchema

router = APIRouter()


@router.post("/check_connection", response_model=ResponseSchema[ConnectionBase])
def check_connection():
    