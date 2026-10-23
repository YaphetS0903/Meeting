from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pwdlib import PasswordHash
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.models import User

passwords = PasswordHash.recommended()
bearer = HTTPBearer(auto_error=False)


def issue_token(user: User) -> str:
    settings = get_settings()
    return jwt.encode(
        {
            "sub": user.id,
            "exp": datetime.now(timezone.utc) + timedelta(minutes=settings.token_minutes),
            "iat": datetime.now(timezone.utc),
            "purpose": "session",
            "auth_version": user.auth_version or 0,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def current_user(
    auth: HTTPAuthorizationCredentials | None = Depends(bearer), db: Session = Depends(get_db)
) -> User:
    if not auth:
        raise HTTPException(401, "请先登录")
    try:
        payload = jwt.decode(auth.credentials, get_settings().jwt_secret, algorithms=["HS256"])
        if payload.get("purpose") != "session":
            raise ValueError("invalid token purpose")
        user = db.get(User, payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(401, "登录已失效") from None
    if user is None or not user.active:
        raise HTTPException(401, "账号已停用")
    if payload.get("auth_version", 0) != (user.auth_version or 0):
        raise HTTPException(401, "密码已变更，请重新登录")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(403, "需要管理员权限")
    return user
