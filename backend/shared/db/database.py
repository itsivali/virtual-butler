import os
import asyncio
from typing import Optional, Dict, Any
from datetime import datetime
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from pymongo import monitoring
from pymongo.errors import (ServerSelectionTimeoutError, OperationFailure,
                            ConnectionFailure, WriteError, PyMongoError)
from motor.motor_asyncio import AsyncIOMotorClient
import structlog

# --- Structured Logging Setup ---
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True
)
logger = structlog.get_logger()

# --- Custom Exceptions ---
class DatabaseError(Exception): pass
class ConnectionError(DatabaseError): pass
class OperationError(DatabaseError): pass

# --- MongoDB Command Listener ---
class MongoDBListener(monitoring.CommandListener):
    def started(self, event):
        logger.info("command_started", command=event.command_name,
                    database=event.database_name, request_id=event.request_id)

    def succeeded(self, event):
        logger.info("command_succeeded", command=event.command_name,
                    duration_ms=getattr(event, "duration_micros", 0) // 1000,
                    request_id=event.request_id)

    def failed(self, event):
        logger.error("command_failed", command=event.command_name,
                     duration_ms=getattr(event, "duration_micros", 0) // 1000,
                     request_id=event.request_id, failure=event.failure)

# --- Database Connection Class ---
class DatabaseConnection:
    # MongoDB settings
    client: Optional[AsyncIOMotorClient] = None
    db: Optional[Any] = None
    collections = {
        "chat_requests": None,
        "work_orders": None,
        "notifications": None,
        "message_threads": None,
        "guest_profiles": None
    }

    # Connection pool settings
    MIN_POOL_SIZE = 10
    MAX_POOL_SIZE = 50
    MAX_IDLE_TIME_MS = 50000

    # Health check settings
    HEALTH_CHECK_INTERVAL = 30  # seconds
    _health_check_task: Optional[asyncio.Task] = None
    _last_health_check: Optional[datetime] = None
    _health_status: Dict[str, Any] = {}

    @classmethod
    async def connect(cls) -> None:
        load_dotenv()
        mongodb_url = os.getenv("MONGODB_URL")
        db_name = os.getenv("MONGODB_DBNAME", "virtualbutler")

        if not mongodb_url:
            raise ConnectionError("MONGODB_URL environment variable is not set")

        try:
            cls.client = AsyncIOMotorClient(
                mongodb_url,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                minPoolSize=cls.MIN_POOL_SIZE,
                maxPoolSize=cls.MAX_POOL_SIZE,
                maxIdleTimeMS=cls.MAX_IDLE_TIME_MS,
                retryWrites=True,
                event_listeners=[MongoDBListener()]
            )
            cls.db = cls.client[db_name]
            await cls._verify_connection()
            await cls._initialize_collections()
            await cls._start_health_monitoring()
            logger.info("database_connected")
        except ConnectionFailure as e:
            logger.error("connection_failure", error=str(e))
            raise ConnectionError(f"MongoDB connection failed: {e}")
        except Exception as e:
            logger.error("unexpected_connection_failure", error=str(e))
            raise

    @classmethod
    async def _verify_connection(cls) -> None:
        if not cls.client:
            raise ConnectionError("Database client is not initialized")
        try:
            await asyncio.wait_for(cls.client.admin.command('ping'), timeout=5.0)
        except asyncio.TimeoutError:
            raise ConnectionError("Database ping timed out")

    @classmethod
    async def _initialize_collections(cls) -> None:
        if cls.db is None:
            raise ConnectionError("Database is not initialized")
        for name in cls.collections:
            cls.collections[name] = cls.db.get_collection(name)

    @classmethod
    @asynccontextmanager
    async def get_connection(cls):
        if not cls.client:
            await cls.connect()
        try:
            yield cls.client
        except PyMongoError as e:
            logger.error("operation_failed", error=str(e))
            raise OperationError(f"Operation failed: {e}") from e

    @classmethod
    async def _start_health_monitoring(cls):
        if not cls._health_check_task:
            cls._health_check_task = asyncio.create_task(cls._health_monitor())
            logger.info("health_monitoring_started")

    @classmethod
    async def _health_monitor(cls):
        while True:
            try:
                cls._health_status = await cls.health_check()
                cls._last_health_check = datetime.utcnow()
                await asyncio.sleep(cls.HEALTH_CHECK_INTERVAL)
            except Exception as e:
                logger.error("health_check_failed", error=str(e))
                await asyncio.sleep(5)

    @classmethod
    async def health_check(cls) -> Dict[str, Any]:
        try:
            start = datetime.utcnow()
            alive = await cls.ping()
            duration = (datetime.utcnow() - start).total_seconds() * 1000
            return {
                "status": "healthy" if alive else "unhealthy",
                "timestamp": datetime.utcnow(),
                "response_time_ms": duration,
                "collections": await cls._collection_stats() if alive else {},
                "last_error": None
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow()
            }

    @classmethod
    async def _collection_stats(cls) -> Dict[str, Any]:
        stats = {}
        if cls.db is None:
            logger.error("collection_stats_failed", error="Database is not initialized")
            return stats
        try:
            names = await cls.db.list_collection_names()
            for name in names:
                try:
                    stats[name] = await cls.db.command("collstats", name)
                except Exception as e:
                    stats[name] = {"error": str(e)}
        except Exception as e:
            logger.error("collection_stats_failed", error=str(e))
        return stats

    @classmethod
    async def ping(cls) -> bool:
        try:
            if not cls.client:
                logger.error("ping_failed", error="Database client is not initialized")
                return False
            await cls.client.admin.command('ping')
            return True
        except Exception as e:
            logger.error("ping_failed", error=str(e))
            return False

    @classmethod
    async def close(cls) -> None:
        try:
            if cls._health_check_task:
                cls._health_check_task.cancel()
                try:
                    await cls._health_check_task
                except asyncio.CancelledError:
                    pass
            if cls.client:
                cls.client.close()
                cls.client = None
                cls._reset()
                logger.info("connection_closed")
        except Exception as e:
            logger.error("close_failed", error=str(e))
            raise

    @classmethod
    def _reset(cls):
        cls.db = None
        cls.collections = {k: None for k in cls.collections}
        cls._health_check_task = None
        cls._last_health_check = None
        cls._health_status = {}
