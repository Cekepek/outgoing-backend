import hmac
import hashlib
import base64
import time
import json
from urllib.parse import quote
from app.config import settings

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


def get_sign_and_session_id(method: str, url: str, body: dict) -> tuple[str, int]:
    signature, agent_session_id = generate_signature(method, url, body)
    return signature, agent_session_id