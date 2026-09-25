import hmac,hashlib
import secrets
from datetime import datetime

def generate_secure_key(prefix: str="mtp", length: int=32) -> str:
    """Generates a URL-safe API key with a prefix."""
    random_part=secrets.token_urlsafe(length)
    return f"{prefix}_{random_part}"

def hash_api_key(api_key: str, secret: str) -> str:
    """Creates a SHA-256 HMAC hash of the API key."""
    return hmac.new(secret.encode(), api_key.encode(), hashlib.sha256).hexdigest()

def now_naive():
    return datetime.now().replace(tzinfo=None)
