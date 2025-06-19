import asyncio
import sys
import os
from datetime import datetime, timedelta
import uuid
from pathlib import Path

# Add the backend directory to the Python path
backend_dir = Path(__file__).parent.parent
sys.path.append(str(backend_dir))

from shared.db.database import Database
from shared.db.models import StatusEnum, PriorityEnum, NotificationTypeEnum

async def seed_database():
    await Database.connect_db()
    
    # Mock data for chat requests
    chat_requests = [
        {
            "request_id": str(uuid.uuid4()),
            "guest_id": "G001",
            "message": "Need extra towels please",
            "status": StatusEnum.PENDING,
            "department": "Housekeeping",
            "tags": ["towels", "housekeeping"],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        },
        {
            "request_id": str(uuid.uuid4()),
            "guest_id": "G002",
            "message": "Room service menu please",
            "status": StatusEnum.COMPLETED,
            "department": "Room Service",
            "tags": ["food", "menu"],
            "created_at": datetime.utcnow() - timedelta(hours=2),
            "updated_at": datetime.utcnow() - timedelta(minutes=30)
        },
        {
            "request_id": str(uuid.uuid4()),
            "guest_id": "G003",
            "message": "AC not working",
            "status": StatusEnum.IN_PROGRESS,
            "department": "Maintenance",
            "tags": ["ac", "repair"],
            "created_at": datetime.utcnow() - timedelta(hours=1),
            "updated_at": datetime.utcnow() - timedelta(minutes=15)
        },
        {
            "request_id": str(uuid.uuid4()),
            "guest_id": "G004",
            "message": "Need late checkout",
            "status": StatusEnum.ASSIGNED,
            "department": "Front Desk",
            "tags": ["checkout", "extension"],
            "created_at": datetime.utcnow() - timedelta(minutes=45),
            "updated_at": datetime.utcnow() - timedelta(minutes=10)
        },
        {
            "request_id": str(uuid.uuid4()),
            "guest_id": "G005",
            "message": "WiFi not connecting",
            "status": StatusEnum.PENDING,
            "department": "IT",
            "tags": ["wifi", "internet"],
            "created_at": datetime.utcnow() - timedelta(minutes=15),
            "updated_at": datetime.utcnow() - timedelta(minutes=15)
        }
    ]
    
    # Mock data for work orders
    work_orders = [
        {
            "request_id": chat_requests[0]["request_id"],
            "guest_id": "G001",
            "staff_id": "S001",
            "description": "Deliver extra towels to Room 301",
            "status": StatusEnum.ASSIGNED,
            "priority": PriorityEnum.MEDIUM,
            "department": "Housekeeping",
            "notes": ["Guest requested 2 bath towels"],
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        },
        {
            "request_id": chat_requests[1]["request_id"],
            "guest_id": "G002",
            "staff_id": "S002",
            "description": "Deliver room service menu to Room 405",
            "status": StatusEnum.COMPLETED,
            "priority": PriorityEnum.LOW,
            "department": "Room Service",
            "completed_at": datetime.utcnow() - timedelta(minutes=30),
            "notes": ["Menu delivered", "Guest thanked staff"],
            "created_at": datetime.utcnow() - timedelta(hours=2),
            "updated_at": datetime.utcnow() - timedelta(minutes=30)
        },
        {
            "request_id": chat_requests[2]["request_id"],
            "guest_id": "G003",
            "staff_id": "S003",
            "description": "Fix AC in Room 512",
            "status": StatusEnum.IN_PROGRESS,
            "priority": PriorityEnum.HIGH,
            "department": "Maintenance",
            "notes": ["Technician en route", "Parts may be needed"],
            "created_at": datetime.utcnow() - timedelta(hours=1),
            "updated_at": datetime.utcnow() - timedelta(minutes=15)
        },
        {
            "request_id": chat_requests[3]["request_id"],
            "guest_id": "G004",
            "staff_id": "S004",
            "description": "Process late checkout for Room 207",
            "status": StatusEnum.ASSIGNED,
            "priority": PriorityEnum.MEDIUM,
            "department": "Front Desk",
            "notes": ["Extended until 2 PM", "Additional charge applied"],
            "created_at": datetime.utcnow() - timedelta(minutes=45),
            "updated_at": datetime.utcnow() - timedelta(minutes=10)
        },
        {
            "request_id": chat_requests[4]["request_id"],
            "guest_id": "G005",
            "staff_id": "S005",
            "description": "Resolve WiFi connection issues in Room 618",
            "status": StatusEnum.PENDING,
            "priority": PriorityEnum.URGENT,
            "department": "IT",
            "notes": ["Guest is business traveler", "Needs immediate attention"],
            "created_at": datetime.utcnow() - timedelta(minutes=15),
            "updated_at": datetime.utcnow() - timedelta(minutes=15)
        }
    ]
    
    # Mock data for notifications
    notifications = [
        {
            "request_id": chat_requests[0]["request_id"],
            "guest_id": "G001",
            "message": "Your towel request has been received",
            "type": NotificationTypeEnum.CHAT,
            "read": False,
            "action_url": "/requests/" + chat_requests[0]["request_id"],
            "metadata": {"department": "Housekeeping"},
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        },
        {
            "request_id": chat_requests[1]["request_id"],
            "guest_id": "G002",
            "message": "Room service menu has been delivered",
            "type": NotificationTypeEnum.WORK_ORDER,
            "read": True,
            "action_url": "/requests/" + chat_requests[1]["request_id"],
            "metadata": {"department": "Room Service", "completed": True},
            "created_at": datetime.utcnow() - timedelta(hours=2),
            "updated_at": datetime.utcnow() - timedelta(minutes=30)
        },
        {
            "request_id": chat_requests[2]["request_id"],
            "guest_id": "G003",
            "message": "Technician is on the way to fix your AC",
            "type": NotificationTypeEnum.SYSTEM,
            "read": False,
            "action_url": "/requests/" + chat_requests[2]["request_id"],
            "metadata": {"department": "Maintenance", "eta": "15 minutes"},
            "created_at": datetime.utcnow() - timedelta(hours=1),
            "updated_at": datetime.utcnow() - timedelta(minutes=15)
        },
        {
            "request_id": chat_requests[3]["request_id"],
            "guest_id": "G004",
            "message": "Late checkout approved until 2 PM",
            "type": NotificationTypeEnum.ALERT,
            "read": False,
            "action_url": "/requests/" + chat_requests[3]["request_id"],
            "metadata": {"department": "Front Desk", "checkout_time": "14:00"},
            "created_at": datetime.utcnow() - timedelta(minutes=45),
            "updated_at": datetime.utcnow() - timedelta(minutes=10)
        },
        {
            "request_id": chat_requests[4]["request_id"],
            "guest_id": "G005",
            "message": "IT support has been notified of your WiFi issue",
            "type": NotificationTypeEnum.SYSTEM,
            "read": False,
            "action_url": "/requests/" + chat_requests[4]["request_id"],
            "metadata": {"department": "IT", "priority": "urgent"},
            "created_at": datetime.utcnow() - timedelta(minutes=15),
            "updated_at": datetime.utcnow() - timedelta(minutes=15)
        }
    ]
    
    # Insert the mock data
    try:
        await Database.chat_requests.insert_many(chat_requests)
        print("Added chat requests")
        
        await Database.work_orders.insert_many(work_orders)
        print("Added work orders")
        
        await Database.notifications.insert_many(notifications)
        print("Added notifications")
        
        print("Database seeded successfully!")
        
    except Exception as e:
        print(f"Error seeding database: {str(e)}")
    finally:
        await Database.close_db()

if __name__ == "__main__":
    asyncio.run(seed_database())