import os
import logging
from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional, Dict, Any
from datetime import datetime
from dotenv import load_dotenv
from pymongo import IndexModel, ASCENDING, DESCENDING
from pymongo.errors import (
    ServerSelectionTimeoutError, 
    OperationFailure, 
    ConnectionFailure,
    WriteError
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class DatabaseConnection:
    client: Optional[AsyncIOMotorClient] = None
    db = None
    chat_requests = None
    work_orders = None
    notifications = None
    message_threads = None
    guest_profiles = None
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseConnection, cls).__new__(cls)
        return cls._instance

    @classmethod
    async def connect(cls) -> None:
        """Initialize database connection and collections."""
        try:
            load_dotenv()
            mongodb_url = os.getenv("MONGODB_URL")
            if not mongodb_url:
                raise ValueError("MONGODB_URL environment variable is not set")

            # Initialize client with timeouts and configurations
            cls.client = AsyncIOMotorClient(
                mongodb_url,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
                maxPoolSize=50,
                retryWrites=True
            )

            # Verify connection
            await cls.client.admin.command('ping')
            logger.info("Successfully connected to MongoDB")

            # Initialize database and collections
            cls.db = cls.client.virtualbutler
            cls.chat_requests = cls.db.chat_requests
            cls.work_orders = cls.db.work_orders
            cls.notifications = cls.db.notifications
            cls.message_threads = cls.db.message_threads
            cls.guest_profiles = cls.db.guest_profiles

            # Create indexes
            await cls._create_indexes()

        except ConnectionFailure as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error during database connection: {str(e)}")
            raise

    @classmethod
    async def close(cls) -> None:
        """Safely close the database connection."""
        try:
            if cls.client:
                cls.client.close()
                cls.client = None
                cls.db = None
                cls.chat_requests = None
                cls.work_orders = None
                cls.notifications = None
                cls.message_threads = None
                cls.guest_profiles = None
                logger.info("Database connection closed successfully")
        except Exception as e:
            logger.error(f"Error closing database connection: {str(e)}")
            raise

    @classmethod
    async def _create_indexes(cls) -> None:
        """Create indexes for all collections."""
        try:
            # Chat Requests indexes
            await cls.chat_requests.create_indexes([
                IndexModel([("request_id", ASCENDING)], unique=True),
                IndexModel([("guest_id", ASCENDING)]),
                IndexModel([("status", ASCENDING)]),
                IndexModel([("department", ASCENDING)]),
                IndexModel([("created_at", DESCENDING)]),
                IndexModel([("guest_id", ASCENDING), ("status", ASCENDING)]),
                IndexModel([("department", ASCENDING), ("status", ASCENDING)])
            ])

            # Work Orders indexes
            await cls.work_orders.create_indexes([
                IndexModel([("work_order_id", ASCENDING)], unique=True),
                IndexModel([("request_id", ASCENDING)]),
                IndexModel([("guest_id", ASCENDING)]),
                IndexModel([("staff_id", ASCENDING)]),
                IndexModel([("status", ASCENDING)]),
                IndexModel([("priority", DESCENDING)]),
                IndexModel([("department", ASCENDING)]),
                IndexModel([("created_at", DESCENDING)]),
                IndexModel([("status", ASCENDING), ("priority", DESCENDING)]),
                IndexModel([("department", ASCENDING), ("status", ASCENDING)])
            ])

            # Notifications indexes
            await cls.notifications.create_indexes([
                IndexModel([("notification_id", ASCENDING)], unique=True),
                IndexModel([("request_id", ASCENDING)]),
                IndexModel([("guest_id", ASCENDING)]),
                IndexModel([("read", ASCENDING)]),
                IndexModel([("type", ASCENDING)]),
                IndexModel([("created_at", DESCENDING)]),
                IndexModel([("guest_id", ASCENDING), ("read", ASCENDING)]),
                IndexModel([("expiry", ASCENDING)], sparse=True)
            ])

            # Message Threads indexes
            await cls.message_threads.create_indexes([
                IndexModel([("thread_id", ASCENDING)], unique=True),
                IndexModel([("request_id", ASCENDING)]),
                IndexModel([("guest_id", ASCENDING)]),
                IndexModel([("staff_id", ASCENDING)]),
                IndexModel([("created_at", DESCENDING)])
            ])

            # Guest Profiles indexes
            await cls.guest_profiles.create_indexes([
                IndexModel([("guest_id", ASCENDING)], unique=True),
                IndexModel([("room_number", ASCENDING)]),
                IndexModel([("email", ASCENDING)], sparse=True),
                IndexModel([("vip_status", DESCENDING)])
            ])

            logger.info("Successfully created all database indexes")

        except OperationFailure as e:
            logger.error(f"Failed to create indexes: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating indexes: {str(e)}")
            raise

    @classmethod
    async def get_db(cls):
        """Get database instance, connecting if necessary."""
        if not cls.client:
            await cls.connect()
        return cls.db

    @classmethod
    async def ping(cls) -> bool:
        """Check database connection health."""
        try:
            if cls.client:
                await cls.client.admin.command('ping')
                return True
            return False
        except Exception as e:
            logger.error(f"Database ping failed: {str(e)}")
            return False

    @classmethod
    async def get_collection_stats(cls) -> Dict[str, Any]:
        """Get statistics for all collections."""
        try:
            stats = {}
            collections = [
                'chat_requests', 'work_orders', 
                'notifications', 'message_threads', 
                'guest_profiles'
            ]
            
            for collection in collections:
                coll = cls.db[collection]
                count = await coll.count_documents({})
                stats[collection] = {
                    'document_count': count,
                    'indexes': await coll.index_information()
                }
            
            return stats
        except Exception as e:
            logger.error(f"Failed to get collection stats: {str(e)}")
            raise

    @classmethod
    async def health_check(cls) -> Dict[str, Any]:
        """Comprehensive health check of the database."""
        try:
            return {
                "status": "healthy" if await cls.ping() else "unhealthy",
                "timestamp": datetime.utcnow(),
                "collections": await cls.get_collection_stats(),
                "connection_pools": cls.client.get_io_loop() if cls.client else None
            }
        except Exception as e:
            logger.error(f"Health check failed: {str(e)}")
            return {
                "status": "unhealthy",
                "error": str(e),
                "timestamp": datetime.utcnow()
            }