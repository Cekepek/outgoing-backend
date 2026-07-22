from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    payment_api_key: str
    payment_api_secret: str
    payment_protocol: str
    payment_host: str
    payment_uri: str
    database_url: str

    class Config:
        env_file = ".env"

settings = Settings()