from fastapi import FastAPI, Request
from pydantic import BaseModel
import uuid, datetime
from pymongo import MongoClient
import os

app = FastAPI()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://mongo:27017"))
db = client.virtualbutler

class RequestModel(BaseModel):
    guestId: str
    text: str

@app.post("/api/v1/request")
async def create_request(data: RequestModel):
    request_id = str(uuid.uuid4())
    db.requests.insert_one({
        "requestId": request_id,
        "guestId": data.guestId,
        "text": data.text,
        "status": "Pending",
        "createdAt": datetime.datetime.utcnow()
    })
    return {"requestId": request_id}

@app.get("/api/v1/status/{request_id}")
async def get_status(request_id: str):
    req = db.requests.find_one({"requestId": request_id})
    if req:
        return {"requestId": request_id, "status": req["status"]}
    return {"error": "Not found"}