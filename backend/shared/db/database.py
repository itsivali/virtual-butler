from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional
import os
import logging
from dotenv import load_dotenv
from pymongo import IndexModel
from pymongo.errors import ServerSelectionTimeoutError, OperationFailure

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class Database:
    client: Optional[AsyncIOMotorClient] = None
    db = None
    chat_requests = None
    work_orders = None
    notifications = None

    @classmethod
    async def connect_db(cls):
        """Connect to MongoDB and initialize collections."""
        try:
            # Load environment variables
            load_dotenv()
            mongodb_url = os.getenv("MONGODB_URL")
            if not mongodb_url:
                raise ValueError("MONGODB_URL environment variable is not set")

            # Initialize client
            cls.client = AsyncIOMotorClient(mongodb_url, 
                                          serverSelectionTimeoutMS=5000,
                                          connectTimeoutMS=10000)
            
            # Verify connection
            await cls.client.server_info()
            
            # Initialize database and collections
            cls.db = cls.client.virtualbutler
            cls.chat_requests = cls.db.chat_requests
            cls.work_orders = cls.db.work_orders
            cls.notifications = cls.db.notifications

            logger.info("Successfully connected to MongoDB")
            
            # Create indexes
            await cls._create_indexes()
            
        except ServerSelectionTimeoutError as e:
            logger.error(f"MongoDB server selection timeout: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error connecting to MongoDB: {str(e)}")
            raise

    @classmethod
    async def close_db(cls):
        """Safely close the MongoDB connection."""
        try:
            if cls.client:
                cls.client.close()
                cls.client = None
                cls.db = None
                cls.chat_requests = None
                cls.work_orders = None
                cls.notifications = None
                logger.info("MongoDB connection closed")
        except Exception as e:
            logger.error(f"Error closing MongoDB connection: {str(e)}")
            raise

    @classmethod
    async def _create_indexes(cls):
        """Create indexes for all collections."""
        try:
            # Ensure collections are initialized
            if cls.chat_requests is not None:
                await cls.chat_requests.create_indexes([
                    IndexModel("request_id", unique=True),
                    IndexModel("guest_id"),
                    IndexModel("status"),
                    IndexModel("created_at"),
                    IndexModel([("guest_id", 1), ("status", 1)])
                ])
            else:
                logger.warning("chat_requests collection is not initialized, skipping index creation.")

            if cls.work_orders is not None:
                await cls.work_orders.create_indexes([
                    IndexModel("request_id", unique=True),
                    IndexModel("guest_id"),
                    IndexModel("staff_id"),
                    IndexModel("status"),
                    IndexModel("priority"),
                    IndexModel("department"),
                    IndexModel([("status", 1), ("department", 1)])
                ])
            else:
                logger.warning("work_orders collection is not initialized, skipping index creation.")

            if cls.notifications is not None:
                await cls.notifications.create_indexes([
                    IndexModel("request_id"),
                    IndexModel("guest_id"),
                    IndexModel("read"),
                    IndexModel("created_at"),
                    IndexModel([("guest_id", 1), ("read", 1)]),
                    IndexModel([("guest_id", 1), ("created_at", -1)])
                ])
            else:
                logger.warning("notifications collection is not initialized, skipping index creation.")

            logger.info("Successfully created database indexes")
            
        except OperationFailure as e:
            logger.error(f"Failed to create indexes: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error creating indexes: {str(e)}")
            raise

    @classmethod
    async def ping(cls):
        """Check database connection."""
        try:
            if cls.client:
                await cls.client.admin.command('ping')
                return True
            return False
        except Exception as e:
            logger.error(f"Database ping failed: {str(e)}")
            return False