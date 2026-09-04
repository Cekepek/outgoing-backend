import redis.asyncio as aioredis
from app.config import settings

# Global async Redis client instance
redis_client = aioredis.from_url(
    settings.redis_url,
    decode_responses=True,
    max_connections=20,
)

async def get_redis_client() -> aioredis.Redis:
    """Dependency or helper to access the Redis client."""
    return redis_client
