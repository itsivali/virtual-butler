import os
import logging
import structlog
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from dotenv import load_dotenv
from pymongo import MonitorListener, monitoring
from pymongo.errors import (
    ServerSelectionTimeoutError, 
    OperationFailure, 
    ConnectionFailure,
    WriteError,
    PyMongoError
)
import asyncio
from contextlib import asynccontextmanager

# Configure structured logging
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

class DatabaseError(Exception):
    """Base class for database errors"""
    pass

class ConnectionError(DatabaseError):
    """Error establishing database connection"""
    pass

class OperationError(DatabaseError):
    """Error performing database operation"""
    pass

class MongoDBListener(MonitorListener):
    """MongoDB command monitoring listener"""
    def started(self, event):
        logger.info("command_started", 
                   command=event.command_name,
                   database=event.database_name,
                   request_id=event.request_id)

    def succeeded(self, event):
        logger.info("command_succeeded",
                   command=event.command_name,
                   duration_ms=event.duration_microseconds // 1000,
                   request_id=event.request_id)

    def failed(self, event):
        logger.error("command_failed",
                    command=event.command_name,
                    duration_ms=event.duration_microseconds // 1000,
                    request_id=event.request_id,
                    failure=event.failure)

class DatabaseConnection:
    # ...existing instance variables...

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
        """Initialize database connection with enhanced monitoring."""
        try:
            load_dotenv()
            mongodb_url = os.getenv("MONGODB_URL")
            if not mongodb_url:
                raise ConnectionError("MONGODB_URL environment variable is not set")

            # Enhanced client configuration
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

            await cls._verify_connection()
            await cls._initialize_collections()
            await cls._start_health_monitoring()

            logger.info("database_connected",
                       pool_size=cls.MAX_POOL_SIZE,
                       min_pool_size=cls.MIN_POOL_SIZE)

        except ConnectionFailure as e:
            error_ctx = {"error": str(e), "error_type": "connection_failure"}
            logger.error("database_connection_failed", **error_ctx)
            raise ConnectionError(f"Failed to connect to MongoDB: {str(e)}") from e
        except Exception as e:
            error_ctx = {"error": str(e), "error_type": "unexpected"}
            logger.error("database_connection_failed", **error_ctx)
            raise

    @classmethod
    async def _verify_connection(cls) -> None:
        """Verify database connection with timeout."""
        try:
            await asyncio.wait_for(
                cls.client.admin.command('ping'),
                timeout=5.0
            )
        except asyncio.TimeoutError:
            raise ConnectionError("Database ping timed out")

    @classmethod
    @asynccontextmanager
    async def get_connection(cls):
        """Get a database connection from the pool."""
        if not cls.client:
            await cls.connect()
        try:
            yield cls.client
        except PyMongoError as e:
            logger.error("database_operation_failed",
                        error=str(e),
                        error_type=type(e).__name__)
            raise OperationError(f"Database operation failed: {str(e)}") from e

    @classmethod
    async def _start_health_monitoring(cls) -> None:
        """Start periodic health monitoring."""
        if cls._health_check_task is None:
            cls._health_check_task = asyncio.create_task(cls._health_monitor())
            logger.info("health_monitoring_started",
                       interval_seconds=cls.HEALTH_CHECK_INTERVAL)

    @classmethod
    async def _health_monitor(cls) -> None:
        """Periodic health monitoring task."""
        while True:
            try:
                cls._health_status = await cls.health_check()
                cls._last_health_check = datetime.utcnow()
                
                if cls._health_status["status"] != "healthy":
                    logger.warning("unhealthy_database",
                                 status=cls._health_status)
                
                # Monitor connection pool
                pool_stats = await cls._get_pool_stats()
                logger.info("connection_pool_stats", **pool_stats)
                
                await asyncio.sleep(cls.HEALTH_CHECK_INTERVAL)
            
            except Exception as e:
                logger.error("health_check_failed",
                           error=str(e),
                           error_type=type(e).__name__)
                await asyncio.sleep(5)  # Shorter interval on failure

    @classmethod
    async def _get_pool_stats(cls) -> Dict[str, Any]:
        """Get connection pool statistics."""
        if not cls.client:
            return {"status": "no_connection"}
        
        return {
            "active_connections": len(cls.client.delegate._topology._servers),
            "min_pool_size": cls.MIN_POOL_SIZE,
            "max_pool_size": cls.MAX_POOL_SIZE,
            "pools": [
                {
                    "address": str(server.description.address),
                    "pool_size": server.pool.size,
                    "active": server.pool.active_sockets,
                }
                for server in cls.client.delegate._topology._servers.values()
            ]
        }

    @classmethod
    async def health_check(cls) -> Dict[str, Any]:
        """Enhanced health check with detailed metrics."""
        try:
            start_time = datetime.utcnow()
            is_alive = await cls.ping()
            response_time = (datetime.utcnow() - start_time).total_seconds() * 1000

            stats = await cls.get_collection_stats() if is_alive else {}
            pool_stats = await cls._get_pool_stats() if is_alive else {}

            health_data = {
                "status": "healthy" if is_alive else "unhealthy",
                "timestamp": datetime.utcnow(),
                "response_time_ms": response_time,
                "collections": stats,
                "connection_pool": pool_stats,
                "last_error": None
            }

            logger.info("health_check_completed",
                       status=health_data["status"],
                       response_time_ms=response_time)

            return health_data

        except Exception as e:
            error_data = {
                "status": "unhealthy",
                "error": str(e),
                "error_type": type(e).__name__,
                "timestamp": datetime.utcnow()
            }
            logger.error("health_check_failed", **error_data)
            return error_data

    @classmethod
    async def close(cls) -> None:
        """Enhanced connection cleanup."""
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
                cls._reset_state()
                logger.info("database_connection_closed")
        except Exception as e:
            logger.error("database_close_failed",
                        error=str(e),
                        error_type=type(e).__name__)
            raise

    @classmethod
    def _reset_state(cls) -> None:
        """Reset all class state variables."""
        cls.db = None
        cls.chat_requests = None
        cls.work_orders = None
        cls.notifications = None
        cls.message_threads = None
        cls.guest_profiles = None
        cls._health_check_task = None
        cls._last_health_check = None
        cls._health_status = {}