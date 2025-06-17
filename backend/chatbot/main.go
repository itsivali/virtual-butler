package main

import (
    "context"
    "encoding/json"
    "fmt"
    "log"
    "math/rand"
    "net/http"
    "os"
    "strings"
    "time"

    "github.com/Azure/azure-sdk-for-go/sdk/messaging/azservicebus"
    "github.com/ivali/virtual-butler/backend/common"
)

type ChatRequest struct {
    GuestID         string `json:"guestID"`
    Text            string `json:"text"`
    VoiceTranscript string `json:"voiceTranscript,omitempty"`
}

type ChatResponse struct {
    RequestID  string `json:"requestID"`
    Status     string `json:"status"`
    Department string `json:"department,omitempty"`
}

var (
    sbSender *azservicebus.Sender
)

var keywordDept = map[string]string{
    "towel": "Housekeeping",
    "clean": "Housekeeping",
    "food": "Room Service",
    "order": "Room Service",
    "checkout": "Front Desk",
    "wifi": "IT",
}

func routeDepartment(text string) string {
    lower := strings.ToLower(text)
    for k, dept := range keywordDept {
        if strings.Contains(lower, k) {
            return dept
        }
    }
    return "General"
}

func handleChatRequest(w http.ResponseWriter, r *http.Request) {
    var req ChatRequest
    if !common.DecodeJSONBody(w, r, &req) {
        return
    }
    requestID := fmt.Sprintf("%d-%d", time.Now().UnixNano(), rand.Intn(10000))
    department := routeDepartment(req.Text + " " + req.VoiceTranscript)
    msg := &azservicebus.Message{ 
        Body: []byte(fmt.Sprintf(`{"requestID":"%s","guestID":"%s","department":"%s","request":"%s"}`,
            requestID, req.GuestID, department, req.Text)),
    }
    go func() {
        if err := sbSender.SendMessage(context.Background(), msg, nil); err != nil {
            log.Printf("Failed to send message to Service Bus: %v", err)
        }
    }()
    resp := &ChatResponse{RequestID: requestID, Status: "received", Department: department}
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(resp)
}

func main() {
    rand.Seed(time.Now().UnixNano())
    sbConnStr := os.Getenv("AZURE_SERVICEBUS_CONNECTION_STRING")
    sbQueue := os.Getenv("AZURE_SERVICEBUS_QUEUE")
    sbClient, err := azservicebus.NewClientFromConnectionString(sbConnStr, nil)
    if err != nil {
        log.Fatalf("Failed to create Service Bus client: %v", err)
    }
    sbSender, err = sbClient.NewSender(sbQueue, nil)
    if err != nil {
        log.Fatalf("Failed to create Service Bus sender: %v", err)
    }

    mux := http.NewServeMux()
    mux.Handle("/api/v1/chat/request", common.CORSMiddleware(common.JWTAuthMiddleware(http.HandlerFunc(handleChatRequest))))
    log.Println("Chat Service running on :8081")
    log.Fatal(http.ListenAndServe(":8081", mux))
}