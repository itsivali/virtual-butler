package main

import (
    "context"
    "encoding/json"
    "log"
    "net/http"
    "os"
    "strings"
    "sync"
    "time"

    "github.com/Azure/azure-sdk-for-go/sdk/messaging/azservicebus"
    "github.com/ivali/virtual-butler/backend/common"
    "go.mongodb.org/mongo-driver/bson/primitive"
    "go.mongodb.org/mongo-driver/mongo"
    "go.mongodb.org/mongo-driver/mongo/options"
)

type WorkOrder struct {
    ID         primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
    GuestID    string             `bson:"guestID" json:"guestID"`
    Department string             `bson:"department" json:"department"`
    Request    string             `bson:"request" json:"request"`
    Status     string             `bson:"status" json:"status"`
    Timestamps struct {
        Created time.Time `bson:"created" json:"created"`
        Updated time.Time `bson:"updated" json:"updated"`
    } `bson:"timestamps" json:"timestamps"`
}

var (
    statusStore = make(map[string]*WorkOrder)
    statusLock  sync.RWMutex
)

func handleStatus(w http.ResponseWriter, r *http.Request) {
    requestID := strings.TrimPrefix(r.URL.Path, "/api/v1/workorder/status/")
    statusLock.RLock()
    resp, ok := statusStore[requestID]
    statusLock.RUnlock()
    if !ok {
        http.Error(w, "Not found", http.StatusNotFound)
        return
    }
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(resp)
}

func workOrderConsumer(ctx context.Context, sbConnStr, sbQueue string, mongoURI, dbName, collName string) {
    client, err := azservicebus.NewClientFromConnectionString(sbConnStr, nil)
    if err != nil {
        log.Fatalf("Failed to create Service Bus client: %v", err)
    }
    receiver, err := client.NewReceiverForQueue(sbQueue, nil)
    if err != nil {
        log.Fatalf("Failed to create Service Bus receiver: %v", err)
    }
    mongoClient, err := mongo.Connect(ctx, options.Client().ApplyURI(mongoURI))
    if err != nil {
        log.Fatalf("Failed to connect to MongoDB: %v", err)
    }
    coll := mongoClient.Database(dbName).Collection(collName)
    for {
        msg, err := receiver.ReceiveMessage(ctx, nil)
        if err != nil {
            log.Printf("Service Bus receive error: %v", err)
            continue
        }
        var payload struct {
            RequestID  string `json:"requestID"`
            GuestID    string `json:"guestID"`
            Department string `json:"department"`
            Request    string `json:"request"`
        }
        if err := json.Unmarshal(msg.Body, &payload); err != nil {
            log.Printf("Invalid message body: %v", err)
            receiver.CompleteMessage(ctx, msg, nil)
            continue
        }
        wo := WorkOrder{
            GuestID:    payload.GuestID,
            Department: payload.Department,
            Request:    payload.Request,
            Status:     "Pending",
        }
        wo.Timestamps.Created = time.Now()
        wo.Timestamps.Updated = time.Now()
        res, err := coll.InsertOne(ctx, wo)
        if err != nil {
            log.Printf("MongoDB insert error: %v", err)
        } else {
            log.Printf("Work order created: %v", res.InsertedID)
            statusLock.Lock()
            statusStore[payload.RequestID] = &wo
            statusLock.Unlock()
        }
        receiver.CompleteMessage(ctx, msg, nil)
        // TODO: Notify notification service
    }
}

func main() {
    sbConnStr := os.Getenv("AZURE_SERVICEBUS_CONNECTION_STRING")
    sbQueue := os.Getenv("AZURE_SERVICEBUS_QUEUE")
    mongoURI := os.Getenv("MONGODB_ATLAS_URI")
    dbName := os.Getenv("MONGODB_DB")
    collName := os.Getenv("MONGODB_COLLECTION")
    go workOrderConsumer(context.Background(), sbConnStr, sbQueue, mongoURI, dbName, collName)

    mux := http.NewServeMux()
    mux.Handle("/api/v1/workorder/status/", common.CORSMiddleware(common.JWTAuthMiddleware(http.HandlerFunc(handleStatus))))
    log.Println("Work-Order Service running on :8082")
    log.Fatal(http.ListenAndServe(":8082", mux))
}