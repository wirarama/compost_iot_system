"""
User accounts: register, sign in, and the signed-in user's own account.

Users authenticate with a Bearer JWT (HS256, 8 hours, no refresh tokens).
This is separate from the devices' `x-key` authentication in main.py: a user
token cannot write readings and a device key cannot reach anything here.
"""
import datetime
import uuid
from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from pwdlib.exceptions import UnknownHashError
from pydantic import BaseModel, BeforeValidator, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from config import settings
from database import db_engine

router = APIRouter(prefix="/auth", tags=["auth"])

JWT_ALGORITHM = "HS256"

password_hasher = PasswordHash.recommended()  # argon2id
# verified against when the email is unknown, so both failures take as long
DUMMY_HASH = password_hasher.hash("not-a-real-password")

# auto_error=False: a missing header is answered below with a 401, not FastAPI's default
bearer = HTTPBearer(auto_error=False)


# ------------------------------------------------------------------ models ---

def _blank_to_none(value):
    if isinstance(value, str):
        value = value.strip()
    return value or None


# the upper bound keeps a huge password from tying up the hasher
Password = Annotated[str, Field(min_length=8, max_length=128)]
DisplayName = Annotated[Annotated[str, Field(max_length=80)] | None, BeforeValidator(_blank_to_none)]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: Password
    display_name: DisplayName = None


class LoginRequest(BaseModel):
    # plain strings: anything that does not match an account is simply a 401
    email: str
    password: str


class ProfileUpdate(BaseModel):
    display_name: DisplayName


class PasswordChange(BaseModel):
    current_password: str
    new_password: Password


class AccountDelete(BaseModel):
    password: str


class User(BaseModel):
    id: uuid.UUID
    email: str
    display_name: str | None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime.datetime
    user: User


# ----------------------------------------------------------------- helpers ---

def _user(row):
    return User(id=row.id, email=row.email, display_name=row.display_name)


def _password_matches(password, password_hash):
    try:
        return password_hasher.verify(password, password_hash)
    except UnknownHashError:
        # a row whose hash is not argon2 (e.g. a placeholder) can never sign in
        password_hasher.verify(password, DUMMY_HASH)
        return False


def _issue_token(row):
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = (now + datetime.timedelta(hours=settings.jwt_expiry_hours)).replace(microsecond=0)
    access_token = jwt.encode(
        {"sub": str(row.id), "iat": now, "exp": expires_at},
        settings.jwt_secret,
        algorithm=JWT_ALGORITHM,
    )
    return Token(access_token=access_token, expires_at=expires_at, user=_user(row))


def _invalid_token():
    # the WWW-Authenticate header marks a token problem; a wrong password
    # (login, change-password, delete) is also a 401 but carries no header
    return HTTPException(
        status_code=401,
        detail="Not authenticated.",
        headers={"WWW-Authenticate": "Bearer"},
    )


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    """The signed-in user's row, from the Bearer token. 401 if anything is off."""
    if credentials is None:
        raise _invalid_token()
    try:
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
        user_id = uuid.UUID(claims["sub"])
    except (jwt.InvalidTokenError, ValueError):
        raise _invalid_token()

    with db_engine.connect() as db:
        row = db.execute(
            text("SELECT id, email, display_name, password_hash FROM users WHERE id = :id"),
            {"id": user_id},
        ).first()
    if row is None:
        raise _invalid_token()
    return row


# --------------------------------------------------------------- endpoints ---

@router.post("/register", status_code=201, response_model=Token)
def register(body: RegisterRequest):
    email = body.email.lower()
    try:
        with db_engine.begin() as db:
            row = db.execute(
                text(
                    """
                    INSERT INTO users (email, password_hash, display_name)
                    VALUES (:email, :password_hash, :display_name)
                    RETURNING id, email, display_name
                    """
                ),
                {
                    "email": email,
                    "password_hash": password_hasher.hash(body.password),
                    "display_name": body.display_name,
                },
            ).first()
    except IntegrityError:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")
    return _issue_token(row)


@router.post("/login", response_model=Token)
def login(body: LoginRequest):
    with db_engine.connect() as db:
        row = db.execute(
            text("SELECT id, email, display_name, password_hash FROM users WHERE lower(email) = :email"),
            {"email": body.email.strip().lower()},
        ).first()

    matches = _password_matches(body.password, row.password_hash if row else DUMMY_HASH)
    if row is None or not matches:
        # the same answer for an unknown email and a wrong password
        raise HTTPException(status_code=401, detail="Email or password is incorrect.")
    return _issue_token(row)


@router.get("/me", response_model=User)
def read_me(user=Depends(current_user)):
    return _user(user)


@router.patch("/me", response_model=User)
def update_me(body: ProfileUpdate, user=Depends(current_user)):
    with db_engine.begin() as db:
        row = db.execute(
            text("UPDATE users SET display_name = :display_name WHERE id = :id RETURNING id, email, display_name"),
            {"display_name": body.display_name, "id": user.id},
        ).first()
    return _user(row)


@router.post("/change-password", status_code=204)
def change_password(body: PasswordChange, user=Depends(current_user)):
    if not _password_matches(body.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")
    with db_engine.begin() as db:
        db.execute(
            text("UPDATE users SET password_hash = :password_hash WHERE id = :id"),
            {"password_hash": password_hasher.hash(body.new_password), "id": user.id},
        )
    return Response(status_code=204)


@router.delete("/me", status_code=204)
def delete_me(body: AccountDelete, user=Depends(current_user)):
    if not _password_matches(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Current password is incorrect.")

    owned_devices = "SELECT id FROM devices WHERE owner_id = :id"
    # one transaction: either the whole account goes, or nothing does
    with db_engine.begin() as db:
        # devices are unpaired, not deleted: keys revoked, assignment closed,
        # no owner, inactive - ready to be paired again by anyone
        db.execute(
            text(f"UPDATE device_apikeys SET revoked_at = now() WHERE revoked_at IS NULL AND device_id IN ({owned_devices})"),
            {"id": user.id},
        )
        db.execute(
            text(f"UPDATE device_bin_assn SET unassigned_at = now() WHERE unassigned_at IS NULL AND device_id IN ({owned_devices})"),
            {"id": user.id},
        )
        db.execute(text("UPDATE devices SET owner_id = NULL, is_active = false WHERE owner_id = :id"), {"id": user.id})
        db.execute(text("DELETE FROM setup_codes WHERE user_id = :id"), {"id": user.id})
        # bins cascade to device_bin_assn, records and bin_events (migration 002)
        db.execute(text("DELETE FROM bins WHERE user_id = :id"), {"id": user.id})
        db.execute(text("DELETE FROM users WHERE id = :id"), {"id": user.id})
    return Response(status_code=204)
