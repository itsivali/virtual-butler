from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.db.models import WorkOrder
from shared.db.database import work_orders
from datetime import datetime

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/v1/work-orders", response_model=WorkOrder)
async def create_work_order(order: WorkOrder):
    order_dict = order.model_dump(by_alias=True)
    await work_orders.insert_one(order_dict)
    return order

@app.get("/api/v1/work-orders/{request_id}", response_model=WorkOrder)
async def get_work_order(request_id: str):
    if (order := await work_orders.find_one({"request_id": request_id})) is not None:
        return order
    raise HTTPException(status_code=404, detail="Work order not found")

@app.get("/api/v1/work-orders/staff/{staff_id}", response_model=list[WorkOrder])
async def get_staff_orders(staff_id: str):
    orders = []
    cursor = work_orders.find({"staff_id": staff_id})
    async for order in cursor:
        orders.append(WorkOrder(**order))
    return orders

@app.put("/api/v1/work-orders/{request_id}", response_model=WorkOrder)
async def update_work_order(request_id: str, order: WorkOrder):
    order.updated_at = datetime.utcnow()
    update_result = await work_orders.update_one(
        {"request_id": request_id},
        {"$set": order.model_dump(by_alias=True)}
    )
    if update_result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Work order not found")
    return order

@app.delete("/api/v1/work-orders/{request_id}")
async def delete_work_order(request_id: str):
    delete_result = await work_orders.delete_one({"request_id": request_id})
    if delete_result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Work order not found")
    return {"message": "Work order deleted"}