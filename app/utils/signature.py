import hmac
import hashlib
import base64
import time
import json
from urllib.parse import quote
from app.config import settings
import random
import string

def generate_signature(method: str, url: str, body: dict) -> tuple[str, int]:
    app_id = settings.payment_api_key         # api-key
    api_secret_b64 = settings.payment_api_secret  # api-secret (base64 encoded)
    padding = 4 - len(api_secret_b64) % 4
    if padding != 4:
        api_secret_b64 += "=" * padding
        
    # 1. Decode api-secret from base64 → ascii (same as Postman)
    api_secret = base64.b64decode(api_secret_b64).decode("ascii")

    # 2. Timestamp (used as both timestamp and nonce)
    timestamp = int(time.time())
    nonce = timestamp  # nonce = requestTimeStamp in their script

    # 3. Encode URL (lowercase)
    encoded_url = quote(url, safe="").lower()

    # 4. MD5 hash the raw body → Base64
    raw_body = json.dumps(body, separators=(",", ":"))
    md5_hash = hashlib.md5(raw_body.encode("utf-8")).digest()
    body_base64 = base64.b64encode(md5_hash).decode("utf-8")

    # 5. Build the string to sign
    # appId + METHOD + encodedUrl + timestamp + nonce + bodyBase64
    data_to_sign = f"{app_id}{method.upper()}{encoded_url}{timestamp}{nonce}{body_base64}"

    # 6. HMAC-SHA256 → Base64
    hmac_hash = hmac.new(
        api_secret.encode("utf-8"),
        data_to_sign.encode("utf-8"),
        hashlib.sha256
    ).digest()
    hmac_base64 = base64.b64encode(hmac_hash).decode("utf-8")

    # 7. Build Authorization header (same format as Postman)
    # "hmacauth appId:hmac:nonce:timestamp"
    authorization = f"hmacauth {app_id}:{hmac_base64}:{nonce}:{timestamp}"

    return authorization, str(int(timestamp))  # return timestamp to use as agentSessionId


def build_request(method: str, url: str, body: dict) -> tuple[str, dict]:
    timestamp = str(int(time.time()))
    body["agentSessionId"] = timestamp 

    signature, _ = generate_signature(method, url, body)

    return signature, body


def generate_agent_txn_id() -> str:
    """
    Generates a unique transaction ID for LightRemit's agentTxnId field.
    Constraint: max 20 characters, string.
    Format: <13-digit epoch millis><6 random uppercase alphanumeric chars> = 19 chars
    """
    timestamp_ms = str(int(time.time() * 1000))  # 13 digits, e.g. 1755000000000
    random_suffix = ''.join(
        random.choices(string.ascii_uppercase + string.digits, k=6)
    )
    txn_id = f"{timestamp_ms}{random_suffix}"
    return txn_id[:20]  # hard safety cap, though this format is naturally 19 chars