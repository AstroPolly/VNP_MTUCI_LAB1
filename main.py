import signal
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict
from fastapi import FastAPI, Header, Query, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict, ValidationError

app = FastAPI(title="MTUSI Lab1 Messenger")

users: Dict[str, dict] = {}
chats: Dict[str, dict] = {}
messages: Dict[str, List[dict]] = {}

MAX_GLOBAL_MESSAGES = 500
global_message_count = 0


class UserCreate(BaseModel):
    model_config = ConfigDict(extra='forbid') # Закрывает претензию "Лишние поля JSON игнорируются"
    username: str = Field(..., pattern=r'^[a-z0-9_]{3,32}$') # Закрывает претензию "Нет проверки формата username"

class UserResponse(BaseModel):
    user_id: str
    display_name: str
    created_at: str

class ChatCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(..., min_length=1, max_length=100) # Было name, стало title по контракту
    member_ids: List[str] = Field(..., min_length=2)      # Было members, стало member_ids

class ChatResponse(BaseModel):
    chat_id: str
    title: str
    member_ids: List[str]
    created_at: str

class MessageCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(..., min_length=1, max_length=4000) # Закрывает претензию "Принимаются пустые и длинные сообщения"

class MessageResponse(BaseModel):
    message_id: str
    chat_id: str
    sender_id: str
    text: str
    created_at: str

class MessagesPage(BaseModel):
    items: List[MessageResponse] 
    cursor: Optional[str] = None


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    req_id = request.headers.get("X-Request-Id", str(uuid.uuid4()))
    request.state.request_id = req_id
    try:
        response = await call_next(request)
        response.headers["X-Request-Id"] = req_id
        return response
    except Exception as exc:
        raise exc


@app.exception_handler(RequestValidationError)
@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: Exception):
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    return JSONResponse(
        status_code=400,
        content={"error": {"code": "400", "message": "Invalid request payload", "request_id": req_id}},
        headers={"X-Request-Id": req_id}
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    req_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    status_code = 404 if exc.status_code == 403 else exc.status_code
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": str(status_code), "message": exc.detail, "request_id": req_id}},
        headers={"X-Request-Id": req_id}
    )


def get_current_user(x_user_id: Optional[str] = Header(None, alias="X-User-Id")):
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Missing X-User-Id")
    if x_user_id not in users:
        raise HTTPException(status_code=401, detail="User not found")
    return x_user_id


@app.post("/api/v1/users", status_code=201, response_model=UserResponse)
def create_user(user: UserCreate):
    for u in users.values():
        if u["display_name"].lower() == user.username.lower():
            raise HTTPException(status_code=409, detail="Username already exists")
    
    user_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    users[user_id] = {"user_id": user_id, "display_name": user.username, "created_at": created_at}
    return users[user_id]

@app.get("/api/v1/users/me", response_model=UserResponse)
def get_current_user_info(current_user_id: str = Depends(get_current_user)):
    return users[current_user_id]

@app.post("/api/v1/chats", status_code=201, response_model=ChatResponse)
def create_chat(chat: ChatCreate, current_user_id: str = Depends(get_current_user)):
    for member_id in chat.member_ids:
        if member_id not in users:
            raise HTTPException(status_code=404, detail=f"User {member_id} not found")
    if current_user_id not in chat.member_ids:
        raise HTTPException(status_code=400, detail="Creator must be a member of the chat")

    chat_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    chats[chat_id] = {"chat_id": chat_id, "title": chat.title, "member_ids": chat.member_ids, "created_at": created_at}
    messages[chat_id] = []
    return chats[chat_id]

@app.get("/api/v1/chats", response_model=List[ChatResponse])
def get_chats(current_user_id: str = Depends(get_current_user)):
    return [chat for chat in chats.values() if current_user_id in chat["member_ids"]]

@app.get("/api/v1/chats/{chat_id}", response_model=ChatResponse)
def get_chat(chat_id: str, current_user_id: str = Depends(get_current_user)):
    if chat_id not in chats or current_user_id not in chats[chat_id]["member_ids"]:
        raise HTTPException(status_code=404, detail="Chat not found") # Маскируем под 404
    return chats[chat_id]

@app.post("/api/v1/chats/{chat_id}/messages", status_code=201, response_model=MessageResponse)
def send_message(chat_id: str, message: MessageCreate, current_user_id: str = Depends(get_current_user)):
    if chat_id not in chats or current_user_id not in chats[chat_id]["member_ids"]:
        raise HTTPException(status_code=404, detail="Chat not found") # Маскируем под 404
    
    global global_message_count
    if global_message_count >= MAX_GLOBAL_MESSAGES:
        raise HTTPException(status_code=503, detail="Service overloaded: global message limit reached")

    msg_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    msg_data = {"message_id": msg_id, "chat_id": chat_id, "sender_id": current_user_id, "text": message.text, "created_at": created_at}
    messages[chat_id].append(msg_data)
    global_message_count += 1
    return msg_data

@app.get("/api/v1/chats/{chat_id}/messages", response_model=MessagesPage)
def get_messages(chat_id: str, current_user_id: str = Depends(get_current_user), limit: int = Query(50, ge=1, le=100), cursor: Optional[str] = None):
    if chat_id not in chats or current_user_id not in chats[chat_id]["member_ids"]:
        raise HTTPException(status_code=404, detail="Chat not found")
    
    chat_msgs = messages.get(chat_id, [])
    start_idx = 0
    if cursor is not None:
        found = False
        for i, msg in enumerate(chat_msgs):
            if msg["message_id"] == cursor:
                start_idx = i + 1
                found = True
                break
        if not found and cursor != "":
            raise HTTPException(status_code=400, detail="Invalid cursor")
    
    result = chat_msgs[start_idx:start_idx + limit]
    next_cursor = result[-1]["message_id"] if len(result) == limit else None # Вернёт null, если это последняя страница
    return {"items": result, "cursor": next_cursor}

@app.get("/health/live")
def health_live():
    return {"status": "ok"}

@app.get("/health/ready")
def health_ready():
    return {"status": "ok"}

def handle_sigterm(signum, frame):
    print("Получен SIGTERM, корректное завершение...", file=sys.stderr)
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_sigterm)