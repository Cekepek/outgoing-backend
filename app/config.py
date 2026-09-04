from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    payment_api_key: str
    payment_api_secret: str
    payment_protocol: str
    payment_host: str
    payment_uri: str
    database_url: str
    payment_host_transferku: str
    payment_client_id_transferku: str
    payment_client_secret_transferku: str
    payment_private_key_transferku: str
    payment_partner_id_transferku: str
    redis_url: str = "redis://127.0.0.1:6380/0"
    redis_cache_ttl: int = 86400

    class Config:
        env_file = ".env"

settings = Settings()