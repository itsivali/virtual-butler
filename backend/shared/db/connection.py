from motor.motor_asyncio import AsyncIOMotorClient
from typing import Optional
import os
from dotenv import load_dotenv
import logging
from pymongo.errors import ConnectionError, ServerSelectionTimeoutError

logger = logging.getLogger(__name__)

class Database:
    client: Optional[AsyncIOMotorClient] = None
    db = None
    chat_requests = None
    work_orders = None
    notifications = None

    @classmethod
    async def connect_db(cls):
        load_dotenv()
        mongodb_url = os.getenv("MONGODB_URL")
        if not mongodb_url:
            raise ValueError("MONGODB_URL environment variable is not set")

        try:
            cls.client = AsyncIOMotorClient(mongodb_url)
            # Verify connection
            await cls.client.server_info()
            cls.db = cls.client.virtualbutler
            
            # Initialize collections
            cls.chat_requests = cls.db.chat_requests
            cls.work_orders = cls.db.work_orders
            cls.notifications = cls.notifications

            logger.info("Successfully connected to MongoDB")
            
            # Create indexes
            await cls._create_indexes()
            
        except (ConnectionError, ServerSelectionTimeoutError) as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            raise

    @classmethod
    async def close_db(cls):
        if cls.client:
            cls.client.close()
            logger.info("MongoDB connection closed")

    @classmethod
    async def _create_indexes(cls):
        try:
            # Chat Requests indexes
            await cls.chat_requests.create_index("request_id", unique=True)
            await cls.chat_requests.create_index("guest_id")
            await cls.chat_requests.create_index("status")
            await cls.chat_requests.create_index("created_at")

            # Work Orders indexes
            await cls.work_orders.create_index("request_id", unique=True)
            await cls.work_orders.create_index("guest_id")
            await cls.work_orders.create_index("staff_id")
            await cls.work_orders.create_index("status")
            await cls.work_orders.create_index("priority")
            await cls.work_orders.create_index("department")

            # Notifications indexes
            await cls.notifications.create_index("request_id")
            await cls.notifications.create_index("guest_id")
            await cls.notifications.create_index("read")
            await cls.notifications.create_index("created_at")
            await cls.notifications.create_index([("guest_id", 1), ("read", 1)])

            logger.info("Successfully created database indexes")
            
        except Exception as e:
            logger.error(f"Failed to create indexes: {str(e)}")
            raise