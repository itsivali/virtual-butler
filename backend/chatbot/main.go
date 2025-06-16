package main

import (
	"encoding/json"
	"fmt"
	"log"
	"math/rand"
	"net/http"
	"strings"
	"sync"
	"time"
)

type ChatRequest struct {
    Message string `json:"message"`
}

type ChatResponse struct {
    ID      string `json:"id"`
    Status  string `json:"status"`
    Reply   string `json:"reply,omitempty"`
}

var (
    statusStore = make(map[string]*ChatResponse)
    statusLock  sync.RWMutex
)

func generateID() string {
    return fmt.Sprintf("%d-%d", time.Now().UnixNano(), rand.Intn(10000))
}

func simpleNLPRouting(msg string) string {
    lower := strings.ToLower(msg)
    switch {
    case strings.Contains(lower, "hello"):
        return "Hello! How can I help you today?"
    case strings.Contains(lower, "weather"):
        return "The weather is sunny."
    case strings.Contains(lower, "time"):
        return time.Now().Format("15:04:05")
    default:
        return "Sorry, I didn't understand that."
    }
}

func handleRequest(w http.ResponseWriter, r *http.Request) {
    var req ChatRequest
    if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
        http.Error(w, "Invalid request", http.StatusBadRequest)
        return
    }
    id := generateID()
    reply := simpleNLPRouting(req.Message)
    resp := &ChatResponse{ID: id, Status: "done", Reply: reply}
    statusLock.Lock()
    statusStore[id] = resp
    statusLock.Unlock()
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(resp)
}

func handleStatus(w http.ResponseWriter, r *http.Request) {
    id := strings.TrimPrefix(r.URL.Path, "/api/v1/status/")
    statusLock.RLock()
    resp, ok := statusStore[id]
    statusLock.RUnlock()
    if !ok {
        http.Error(w, "Not found", http.StatusNotFound)
        return
    }
    w.Header().Set("Content-Type", "application/json")
    json.NewEncoder(w).Encode(resp)
}

func main() {
    rand.Seed(time.Now().UnixNano())
    http.HandleFunc("/api/v1/request", handleRequest)
    http.HandleFunc("/api/v1/status/", handleStatus)
    log.Println("Chatbot service running on :8080")
    log.Fatal(http.ListenAndServe(":8080", nil))
}
