from datetime import timezone
from app.models import User
from datetime import datetime
from fastapi import HTTPException
from app.models import SessionModel
from app.database import get_db
from fastapi import Depends
from sqlalchemy.orm import Session
from fastapi import Header
async def get_current_session(
    authorization: str = Header(...),
    db: Session = Depends(get_db),
) -> SessionModel:
    session_id = authorization.removeprefix("Bearer ").strip()
    session = db.query(SessionModel).filter(SessionModel.session_id == session_id).first()
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session")
    if session.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Session expired")
    return session

async def get_current_user(
    session: SessionModel = Depends(get_current_session),
    db: Session = Depends(get_db),
) -> User:
    user = db.query(User).filter(User.id == session.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user