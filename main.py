import signal
import sys
import uuid
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict

app = FastAPI(title="MTUSI Lab1 Messenger")

users: Dict[str, dict] = {}
chats: Dict[str, dict] = {}
messages: Dict[str, List[dict]] = {}

MAX_MESSAGES_PER_CHAT = 500

class UserCreate(BaseModel):
    username: str

class ChatCreate(BaseModel):
    name: str
    members: List[str]

class MessageCreate(BaseModel):
    text: str

@app.post("/users")
def create_user(user: UserCreate):
    user_id = str(uuid.uuid4())
    users[user_id] = {"username": user.username}
    return {"user_id": user_id}

@app.post("/chats")
def create_chat(chat: ChatCreate):
    for member_id in chat.members:
        if member_id not in users:
            raise HTTPException(status_code=404, detail=f"User {member_id} not found")
            
    chat_id = str(uuid.uuid4())
    chats[chat_id] = {"name": chat.name, "members": chat.members}
    messages[chat_id] = []
    return {"chat_id": chat_id}

@app.post("/chats/{chat_id}/messages")
def send_message(chat_id: str, message: MessageCreate, x_user_id: str = Header(..., alias="X-User-Id")):
    if x_user_id not in users:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if chat_id not in chats:
        raise HTTPException(status_code=404, detail="Chat not found")
    if x_user_id not in chats[chat_id]["members"]:
        raise HTTPException(status_code=403, detail="Forbidden")
    
    if len(messages[chat_id]) >= MAX_MESSAGES_PER_CHAT:
        raise HTTPException(status_code=503, detail="Service overloaded: message limit reached")

    msg_id = str(uuid.uuid4())
    msg_data = {"id": msg_id, "text": message.text, "user_id": x_user_id}
    messages[chat_id].append(msg_data)
    return {"message_id": msg_id}

@app.get("/chats/{chat_id}/messages")
def get_messages(chat_id: str, x_user_id: str = Header(..., alias="X-User-Id"), limit: int = Query(10), cursor: Optional[str] = None):
    if x_user_id not in users:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if chat_id not in chats:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    chat_msgs = messages.get(chat_id, [])
    start_idx = 0
    if cursor:
        for i, msg in enumerate(chat_msgs):
            if msg["id"] == cursor:
                start_idx = i + 1
                break
    
    result = chat_msgs[start_idx:start_idx + limit]
    next_cursor = result[-1]["id"] if result else None
    return {"messages": result, "next_cursor": next_cursor}

@app.get("/health/ready")
def health_check():
    return {"status": "ready"}

def handle_sigterm(signum, frame):
    print("Получен SIGTERM, корректное завершение...")
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)