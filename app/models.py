from datetime import date, datetime
 
from sqlalchemy import Date, DateTime, ForeignKey, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
 
 
class Base(DeclarativeBase):
    pass
 
 
class User(Base):
    __tablename__ = "users"
 
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    pin_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
 
    sender: Mapped["Sender"] = relationship(back_populates="user", uselist=False, cascade="all, delete-orphan")
    session: Mapped["SessionModel"] = relationship(back_populates="user", uselist=True, cascade="all, delete-orphan")
    activity_logs: Mapped[list["ActivityLog"]] = relationship(back_populates="user", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"

class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user: Mapped["User"] = relationship(back_populates="session")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class ActivityLog(Base):
    __tablename__ = "activity_logs"
    
    id_log: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    aktivitas: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    user: Mapped["User"] = relationship(back_populates="activity_logs")
 
 
class Sender(Base):
    __tablename__ = "senders"
 
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    user: Mapped["User"] = relationship(back_populates="sender")
 
    # --- Identity ---
    sender_customer_type: Mapped[str] = mapped_column(String(1), nullable=False)  # "B" | "I"
    sender_first_name: Mapped[str | None] = mapped_column(String(100))
    sender_middle_name: Mapped[str | None] = mapped_column(String(100))
    sender_last_name: Mapped[str | None] = mapped_column(String(100))
    sender_company_name: Mapped[str | None] = mapped_column(String(255))
    sender_company_reg_number: Mapped[str | None] = mapped_column(String(100))
    sender_company_incorporate_date: Mapped[date | None] = mapped_column(Date)
    sender_gender: Mapped[str | None] = mapped_column(String(1))
    sender_native_first_name: Mapped[str | None] = mapped_column(String(150))
    sender_native_last_name: Mapped[str | None] = mapped_column(String(150))
 
    # --- Contact & Address ---
    sender_address: Mapped[str | None] = mapped_column(String(255))
    sender_city: Mapped[str | None] = mapped_column(String(100))
    sender_state: Mapped[str | None] = mapped_column(String(100))
    sender_zip_code: Mapped[str | None] = mapped_column(String(20))
    sender_country: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO-3, e.g. IDN
    sender_mobile: Mapped[str | None] = mapped_column(String(30))
    sender_email: Mapped[str | None] = mapped_column(String(255))
    sender_nationality: Mapped[str | None] = mapped_column(String(3))  # ISO-3
 
    # --- ID Document ---
    sender_id_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sender_id_number: Mapped[str] = mapped_column(String(100), nullable=False)
    sender_id_issue_country: Mapped[str | None] = mapped_column(String(3))
    sender_id_issue_date: Mapped[date | None] = mapped_column(Date)
    sender_id_expire_date: Mapped[date | None] = mapped_column(Date)
    sender_date_of_birth: Mapped[date | None] = mapped_column(Date)
    sender_secondary_id_type: Mapped[str | None] = mapped_column(String(50))
    sender_secondary_id_number: Mapped[str | None] = mapped_column(String(100))
 
    # --- Transaction Info ---
    sender_occupation: Mapped[str | None] = mapped_column(String(100))
 
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
 
    def __repr__(self) -> str:
        return f"<Sender id={self.id} user_id={self.user_id} type={self.sender_customer_type!r}>"