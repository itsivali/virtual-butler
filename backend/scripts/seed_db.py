import asyncio
import sys
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from colorama import init, Fore, Style

# Initialize colorama
init(autoreset=True)

# Setup import paths
backend_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(backend_dir))

from shared.db.database import DatabaseConnection as Database
from shared.db.models import StatusEnum, PriorityEnum, NotificationTypeEnum

async def insert_documents_bulk(collection, documents, label):
    try:
        if documents:
            await collection.insert_many(documents)
            print(f"{Fore.GREEN}Inserted {len(documents)} documents into {label} collection.")
        else:
            print(f"{Fore.YELLOW}No documents to insert for {label}.")
    except Exception as e:
        print(f"{Fore.RED}Failed to insert into {label}: {e}")

async def seed_database():
    await Database.connect()

    now = datetime.utcnow()

    chat_requests = [
        {"guest_id": f"G00{i+1}", "message": msg, "status": status,
         "department": dept, "tags": tags,
         "created_at": now - timedelta(hours=i), "updated_at": now - timedelta(minutes=i * 15),
         "request_id": str(uuid.uuid4())}
        for i, (msg, status, dept, tags) in enumerate([
            ("Need extra towels please", StatusEnum.PENDING, "Housekeeping", ["towels", "housekeeping"]),
            ("Room service menu please", StatusEnum.COMPLETED, "Room Service", ["food", "menu"]),
            ("AC not working", StatusEnum.IN_PROGRESS, "Maintenance", ["ac", "repair"]),
            ("Need late checkout", StatusEnum.ASSIGNED, "Front Desk", ["checkout", "extension"]),
            ("WiFi not connecting", StatusEnum.PENDING, "IT", ["wifi", "internet"]),
        ])
    ]

    work_orders = [
        {"request_id": req["request_id"], "guest_id": req["guest_id"], "staff_id": f"S00{i+1}",
         "description": desc, "status": status, "priority": priority, "department": dept,
         "notes": notes, "created_at": req["created_at"], "updated_at": req["updated_at"],
         "completed_at": req["updated_at"] if status == StatusEnum.COMPLETED else None}
        for i, (req, desc, status, priority, dept, notes) in enumerate(zip(
            chat_requests,
            [
                "Deliver extra towels to Room 301",
                "Deliver room service menu to Room 405",
                "Fix AC in Room 512",
                "Process late checkout for Room 207",
                "Resolve WiFi connection issues in Room 618"
            ],
            [StatusEnum.ASSIGNED, StatusEnum.COMPLETED, StatusEnum.IN_PROGRESS, StatusEnum.ASSIGNED, StatusEnum.PENDING],
            [PriorityEnum.MEDIUM, PriorityEnum.LOW, PriorityEnum.HIGH, PriorityEnum.MEDIUM, PriorityEnum.URGENT],
            ["Housekeeping", "Room Service", "Maintenance", "Front Desk", "IT"],
            [
                ["Guest requested 2 bath towels"],
                ["Menu delivered", "Guest thanked staff"],
                ["Technician en route", "Parts may be needed"],
                ["Extended until 2 PM", "Additional charge applied"],
                ["Guest is business traveler", "Needs immediate attention"]
            ]
        ))
    ]

    notifications = [
        {"request_id": req["request_id"], "guest_id": req["guest_id"], "message": msg,
         "type": type_, "read": read, "action_url": f"/requests/{req['request_id']}",
         "metadata": meta, "created_at": req["created_at"], "updated_at": req["updated_at"]}
        for req, msg, type_, read, meta in zip(
            chat_requests,
            [
                "Your towel request has been received",
                "Room service menu has been delivered",
                "Technician is on the way to fix your AC",
                "Late checkout approved until 2 PM",
                "IT support has been notified of your WiFi issue"
            ],
            [NotificationTypeEnum.CHAT, NotificationTypeEnum.WORK_ORDER, NotificationTypeEnum.SYSTEM, NotificationTypeEnum.ALERT, NotificationTypeEnum.SYSTEM],
            [False, True, False, False, False],
            [
                {"department": "Housekeeping"},
                {"department": "Room Service", "completed": True},
                {"department": "Maintenance", "eta": "15 minutes"},
                {"department": "Front Desk", "checkout_time": "14:00"},
                {"department": "IT", "priority": "urgent"}
            ]
        )
    ]

    try:
        await insert_documents_bulk(Database.collections["chat_requests"], chat_requests, "chat_requests")
        await insert_documents_bulk(Database.collections["work_orders"], work_orders, "work_orders")
        await insert_documents_bulk(Database.collections["notifications"], notifications, "notifications")
        print(f"{Fore.GREEN}{Style.BRIGHT}Database seeded successfully!")
    except Exception as e:
        print(f"{Fore.RED}Error seeding database: {e}")
    finally:
        await Database.close()

if __name__ == "__main__":
    asyncio.run(seed_database())
