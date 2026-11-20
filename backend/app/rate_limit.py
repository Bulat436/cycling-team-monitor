from sqlalchemy import delete
from sqlalchemy.orm import Session

from .models import LoginLimit
from .security import token_hash

WINDOW_SECONDS = 15 * 60


def reserve_login_attempt(db: Session, ip: str, email: str, timestamp: int) -> int:
    db.execute(delete(LoginLimit).where(LoginLimit.expires_at <= timestamp))
    limits = {
        "ip:" + token_hash(ip): 50,
        "account:" + token_hash(ip + "\0" + email): 5,
    }
    buckets = {key: db.get(LoginLimit, key) for key in limits}
    retry_after = max(
        (bucket.expires_at - timestamp for key, bucket in buckets.items()
         if bucket and bucket.attempts >= limits[key]),
        default=0,
    )

    if not retry_after:
        for key, bucket in buckets.items():
            if bucket:
                bucket.attempts += 1
            else:
                db.add(LoginLimit(key=key, attempts=1, expires_at=timestamp + WINDOW_SECONDS))
    # Release the write lock before expensive Argon2 verification.
    db.commit()
    return retry_after
