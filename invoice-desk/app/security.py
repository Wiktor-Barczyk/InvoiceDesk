import base64
import hashlib
import hmac
import json
import secrets
import time


def hash_password(password):
    salt = secrets.token_hex(12)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 1_000_000)
    return f"pbkdf2_sha256$1000000${salt}${base64.b64encode(digest).decode()}"


def verify_password(password, encoded):
    try:
        scheme, rounds, salt, expected = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256" or not 10000 <= int(rounds) <= 5_000_000:
            return False
        value = base64.b64encode(hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))).decode()
        return hmac.compare_digest(value, expected)
    except (ValueError, TypeError):
        return False


def sign_session(user_id, secret):
    data = {"id": user_id, "exp": int(time.time()) + 8 * 3600, "csrf": secrets.token_urlsafe(24)}
    raw = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    signature = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()
    return raw + "." + signature


def read_session(token, secret):
    try:
        raw, signature = token.rsplit(".", 1)
        if not hmac.compare_digest(hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest(), signature):
            return None
        data = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
        return data if data["exp"] > time.time() else None
    except (ValueError, KeyError, TypeError):
        return None
