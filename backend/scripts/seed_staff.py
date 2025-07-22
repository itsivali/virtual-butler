"""
Script to seed staff users into the database for testing the chatbot and work order assignment.
"""
import asyncio
from datetime import datetime, timezone
from shared.db.database import DatabaseConnection

STAFF_USERS = [
    {
        "staff_id": "staff1",
        "name": "Alice Smith",
        "email": "alice.smith@example.com",
        "role": "staff",
        "department": "HOUSEKEEPING",
        "created_at": datetime.now(timezone.utc),
    },
    {
        "staff_id": "staff2",
        "name": "Bob Johnson",
        "email": "bob.johnson@example.com",
        "role": "staff",
        "department": "MAINTENANCE",
        "created_at": datetime.now(timezone.utc),
    },
    {
        "staff_id": "admin1",
        "name": "Carol Admin",
        "email": "carol.admin@example.com",
        "role": "admin",
        "department": "FRONT_DESK",
        "created_at": datetime.now(timezone.utc),
    },
]

async def seed_staff():
    async with DatabaseConnection.get_connection() as conn:
        if conn is None:
            raise RuntimeError("Database connection failed (conn is None)")
        staff_collection = conn["virtualbutler"]["staff_profiles"]
        for staff in STAFF_USERS:
            await staff_collection.update_one(
                {"staff_id": staff["staff_id"]},
                {"$set": staff},
                upsert=True
            )
    print("Seeded staff users.")

if __name__ == "__main__":
    asyncio.run(seed_staff())
