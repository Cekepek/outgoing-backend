from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ActivityLog, Sender, SessionModel, User
from app.schemas import LoginSchema, RegisterSchema
from app.services.apiService import (
    LOGIN_RATE_LIMIT,
    clear_failed_attempts,
    generate_session_token,
    get_current_session,
    get_current_user,
    hash_secret,
    is_rate_limited,
    prune_expired_sessions,
    record_failed_attempt,
    verify_secret_constant_time,
)

router = APIRouter()


@router.post("/login")
async def login(
    login_data: LoginSchema,
    request: Request,
    db: Session = Depends(get_db),
):
    prune_expired_sessions(db)

    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    username_key = f"user:{login_data.username.strip().lower()}"
    ip_key = f"ip:{client_ip or 'unknown'}"

    if await is_rate_limited(LOGIN_RATE_LIMIT, username_key) or await is_rate_limited(
        LOGIN_RATE_LIMIT, ip_key
    ):
        return JSONResponse(
            status_code=429,
            content={
                "status": "failed",
                "message": "Terlalu banyak percobaan login gagal. Silakan coba lagi nanti.",
            },
        )

    user = db.query(User).filter(User.username == login_data.username).first()
    password_ok = verify_secret_constant_time(
        login_data.password,
        user.password_hash if user else None,
    )

    if not user or not password_ok:
        await record_failed_attempt(LOGIN_RATE_LIMIT, username_key)
        await record_failed_attempt(LOGIN_RATE_LIMIT, ip_key)
        if user:
            failed_log = ActivityLog(
                user_id=user.id,
                aktivitas="Gagal melakukan login: password salah",
                ip_address=client_ip,
                user_agent=user_agent,
            )
            db.add(failed_log)
            db.commit()

        return JSONResponse(
            status_code=401,
            content={
                "status": "failed",
                "message": "Username atau password salah",
            },
        )

    if not user.is_active:
        return JSONResponse(
            status_code=403,
            content={
                "status": "failed",
                "message": "Akun tidak aktif",
            },
        )

    await clear_failed_attempts(LOGIN_RATE_LIMIT, username_key)
    await clear_failed_attempts(LOGIN_RATE_LIMIT, ip_key)

    # Cap active concurrent sessions per user to 5 newest
    active_sessions = (
        db.query(SessionModel)
        .filter(SessionModel.user_id == user.id)
        .order_by(SessionModel.created_at.desc())
        .all()
    )
    if len(active_sessions) >= 5:
        for old_session in active_sessions[4:]:
            db.delete(old_session)

    raw_token, token_hash = generate_session_token()
    session = SessionModel(
        session_id=token_hash,
        user_id=user.id,
        ip_address=client_ip,
        user_agent=user_agent,
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(session)

    activity = ActivityLog(
        user_id=user.id,
        aktivitas="Pengguna melakukan login",
        ip_address=client_ip,
        user_agent=user_agent,
    )
    db.add(activity)
    db.commit()

    return {
        "status": "success",
        "message": "Login berhasil",
        "data": {
            "session_id": raw_token,
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
            }
            if user.sender
            else None,
        },
    }


@router.post("/logout")
async def logout(
    request: Request,
    session: SessionModel = Depends(get_current_session),
    db: Session = Depends(get_db),
):
    user_id = session.user_id
    db.delete(session)
    activity = ActivityLog(
        user_id=user_id,
        aktivitas="Pengguna melakukan logout",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(activity)
    db.commit()
    return {"status": "success", "message": "Logout berhasil"}


@router.post("/logout-all")
async def logout_all(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    db.query(SessionModel).filter(SessionModel.user_id == current_user.id).delete()
    activity = ActivityLog(
        user_id=current_user.id,
        aktivitas="Pengguna melakukan logout dari semua perangkat",
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    db.add(activity)
    db.commit()
    return {"status": "success", "message": "Berhasil logout dari semua sesi"}

@router.post("/register")
def register(data: RegisterSchema, db: Session = Depends(get_db)):
    try:
        existing = db.query(User).filter(User.username == data.username).first()
        if existing:
            return {"status": "failed", "message": "Username sudah digunakan"}

        user = User(
            username=data.username,
            password_hash=hash_secret(data.password),
            pin_hash=hash_secret(data.pin),
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