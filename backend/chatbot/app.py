from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from ..shared.db.models import ChatRequest, StatusEnum
from ..shared.db.database import Database
import uuid
from datetime import datetime

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup_db_client():
    await Database.connect_db()

    if Database.db is not None and (not hasattr(Database, "chat_requests") or Database.chat_requests is None):
        Database.chat_requests = Database.db["chat_requests"]

@app.on_event("shutdown")
async def shutdown_db_client():
    await Database.close_db()

@app.post("/api/v1/request")
async def create_request(request: ChatRequest):
    try:

        if Database.db is None:
            await Database.connect_db()
        if not hasattr(Database, "chat_requests") or Database.chat_requests is None:
            if Database.db is not None:
                Database.chat_requests = Database.db["chat_requests"]
            else:
                raise HTTPException(status_code=500, detail="Database connection is not initialized.")

        request_dict = {
            "request_id": str(uuid.uuid4()),
            "guest_id": request.guest_id,
            "message": request.message,
            "status": StatusEnum.PENDING,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        await Database.chat_requests.insert_one(request_dict)
        return {"requestId": request_dict["request_id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/v1/status/{request_id}")
async def get_status(request_id: str):
    try:

        if Database.db is None:
            await Database.connect_db()
        if not hasattr(Database, "chat_requests") or Database.chat_requests is None:
            if Database.db is not None:
                Database.chat_requests = Database.db["chat_requests"]
            else:
                raise HTTPException(status_code=500, detail="Database connection is not initialized.")

        request = await Database.chat_requests.find_one({"request_id": request_id})
        if request:
            return {
                "requestId": request_id,
                "status": request["status"],
                "department": request.get("department")
            }
        raise HTTPException(status_code=404, detail="Request not found")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health_check():
    try:
        # Check database connection
        if Database.client:
            await Database.ping()
            return {"status": "healthy"}
        return {"status": "database not connected"}
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Service unhealthy: {str(e)}"
        )