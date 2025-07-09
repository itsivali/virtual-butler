"""
Script to seed a secure test guest user for end-to-end Conversational Language Understanding (CLU) bot testing.
- Passwords are hashed using bcrypt.
- Guest profile includes a PIN for authentication.
- Use this for secure, realistic chatbot testing.
"""
import asyncio
from datetime import datetime, timezone
from passlib.context import CryptContext
from shared.db.database import DatabaseConnection

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

GUEST_USER = {
    "guest_id": "guest1",
    "name": "Test Guest",
    "room_number": "101",
    "pin": pwd_context.hash("1234"),
    "email": "guest1@example.com",
    "phone": "+1234567890",
    "created_at": datetime.now(timezone.utc),
}

async def seed_guest():
    async with DatabaseConnection.get_connection() as conn:
        guest_collection = conn["virtualbutler"]["guest_profiles"]
        await guest_collection.update_one(
            {"guest_id": GUEST_USER["guest_id"]},
            {"$set": GUEST_USER},
            upsert=True
        )
    print("Seeded secure guest user.")

if __name__ == "__main__":
    asyncio.run(seed_guest())
