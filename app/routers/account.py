from app.models import Sender
from app.schemas import RegisterSchema
from app.models import ActivityLog
from datetime import timedelta
from datetime import datetime
from app.models import SessionModel
import uuid
from app.models import User
from app.database import get_db
from fastapi import Depends
from sqlalchemy.orm import Session
from app.schemas import LoginSchema
from typing import Any
from fastapi import APIRouter, HTTPException
import httpx
from sqlalchemy import null

from app.schemas import BankItem, BankRequest, BaseResponse, CatalogueItem, CatalogueRequest, ErrorItems, ExchangeRateItem, RateItem, RateItemSuccess, RateRequest, ResponseSchema
from app.config import settings
from app.utils.signature import build_request
from passlib.hash import bcrypt

router = APIRouter()

SESSIONS = {}

@router.post("/login")
def login(
    login_data: LoginSchema,
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.username == login_data.username).first()

    if not user:
        return {
            "status": "failed",
            "message": "Akun tidak ditemukan"
        }

    if not bcrypt.verify(login_data.password, user.password_hash):
        return {
            "status": "failed",
            "message": "Password Salah"
        }

    session_id = str(uuid.uuid4())
    session = SessionModel(
        session_id=session_id,
        user_id=user.id,
        expires_at=datetime.utcnow() + timedelta(days=1)
    )
    db.add(session)

    activity = ActivityLog(
        user_id=user.id,
        aktivitas="Pengguna melakukan login",
    )
    db.add(activity)
    db.commit()

    return {
        "status": "success",
        "message": "Login berhasil",
        "data": {
            "session_id": session_id,
            "user": {
                "id": user.id,
                "username": user.username,
            },
            "has_sender_profile": user.sender is not None,
            "sender": {
                "sender_customer_type": user.sender.sender_customer_type,
                "sender_id_number": user.sender.sender_id_number,
                "sender_first_name": user.sender.sender_first_name,
                "sender_last_name": user.sender.sender_last_name,
                "sender_company_name": user.sender.sender_company_name,
                "sender_country": user.sender.sender_country,
                "sender_mobile": user.sender.sender_mobile,
                "sender_email": user.sender.sender_email,
                # only the fields the app actually needs immediately
            } if user.sender else None
        }
    }

@router.post("/register")
def register(data: RegisterSchema, db: Session = Depends(get_db)):
    try:
        existing = db.query(User).filter(User.username == data.username).first()
        if existing:
            return {"status": "failed", "message": "Username sudah digunakan"}

        user = User(
            username=data.username,
            password_hash=bcrypt.hash(data.password),
            pin_hash=bcrypt.hash(data.pin),
        )
        db.add(user)
        db.flush()  # get user.id before creating sender

        sender = Sender(
            user_id=user.id,
            sender_customer_type=data.sender_customer_type,
            sender_first_name=data.sender_first_name,
            sender_middle_name=data.sender_middle_name,
            sender_last_name=data.sender_last_name,
            sender_company_name=data.sender_company_name,
            sender_country=data.sender_country,
            sender_id_type=data.sender_id_type,
            sender_id_number=data.sender_id_number,
            sender_address=data.sender_address,
            sender_city=data.sender_city,
            sender_state=data.sender_state,
            sender_zip_code=data.sender_zip_code,
            sender_nationality=data.sender_nationality,
            sender_id_issue_country=data.sender_id_issue_country,
            sender_id_issue_date=data.sender_id_issue_date,
            sender_id_expire_date=data.sender_id_expire_date,
            sender_date_of_birth=data.sender_date_of_birth,
            sender_secondary_id_type=data.sender_secondary_id_type,
            sender_secondary_id_number=data.sender_secondary_id_number,
            sender_occupation=data.sender_occupation,
            sender_mobile=data.sender_mobile,
            sender_email=data.sender_email,
            sender_company_reg_number=data.sender_company_reg_number,
            sender_company_incorporate_date=data.sender_company_incorporate_date,
            sender_gender=data.sender_gender,
            sender_native_first_name=data.sender_native_first_name,
            sender_native_last_name=data.sender_native_last_name,
        )
        db.add(sender)
        db.commit()

        return {"status": "success", "message": "Registrasi berhasil"}
    except Exception as e:
        return {"status": "failed", "message": str(e)}