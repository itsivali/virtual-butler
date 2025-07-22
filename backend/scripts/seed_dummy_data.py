"""
Script to seed the database with dummy data for all collections.
Run: python backend/scripts/seed_dummy_data.py
"""
import asyncio
from datetime import datetime
from shared.db.database import DatabaseConnection
from shared.db.models import GuestProfile, User, Notification, ChatRequest, DepartmentEnum, StatusEnum, NotificationTypeEnum

async def seed_guests(conn):
    guests = [
        GuestProfile(guest_id="g1", name="Alice Smith", email="alice@example.com", phone="1234567890", room_number="101").dict(),
        GuestProfile(guest_id="g2", name="Bob Lee", email="bob@example.com", phone="2345678901", room_number="102").dict(),
    ]
    await conn["guest_profiles"].delete_many({})
    await conn["guest_profiles"].insert_many(guests)

async def seed_users(conn):
    users = [
        User(
            username="alice",
            first_name="Alice",
            last_name="Smith",
            room_id="101",
            chat_history=[],
            check_in_date=datetime(2025, 7, 20, 15, 0, 0)
        ).dict(by_alias=True),
        User(
            username="bob",
            first_name="Bob",
            last_name="Lee",
            room_id="102",
            chat_history=[],
            check_in_date=datetime(2025, 7, 21, 16, 0, 0)
        ).dict(by_alias=True),
    ]
    await conn["users"].delete_many({})
    await conn["users"].insert_many(users)

async def seed_notifications(conn):
    notifications = [
        Notification(
            notification_id="n1",
            request_id="req1",
            guest_id="g1",
            type=NotificationTypeEnum.CHAT,
            message="Welcome Alice!",
            created_at=datetime.utcnow(),
        ).dict(by_alias=True),
        Notification(
            notification_id="n2",
            request_id="req2",
            guest_id="g2",
            type=NotificationTypeEnum.CHAT,
            message="Your room is ready.",
            created_at=datetime.utcnow(),
        ).dict(by_alias=True),
    ]
    await conn["notifications"].delete_many({})
    await conn["notifications"].insert_many(notifications)

async def seed_chat_requests(conn):
    chats = [
        ChatRequest(
            request_id="req1",
            guest_id="g1",
            message="Can I get extra towels?",
            department=DepartmentEnum.FRONT_DESK,
            status=StatusEnum.PENDING,
            sentiment=0.5,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        ).model_dump(by_alias=True),
        ChatRequest(
            request_id="req2",
            guest_id="g2",
            message="What time is breakfast?",
            department=DepartmentEnum.FRONT_DESK,
            status=StatusEnum.PENDING,
            sentiment=0.2,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        ).model_dump(by_alias=True),
    ]
    await conn["chat_requests"].delete_many({})
    await conn["chat_requests"].insert_many(chats)

async def main():
    async with DatabaseConnection.get_connection() as conn:
        await seed_guests(conn)
        await seed_users(conn)
        await seed_notifications(conn)
        await seed_chat_requests(conn)
    print("Dummy data seeded successfully.")

if __name__ == "__main__":
    asyncio.run(main())
