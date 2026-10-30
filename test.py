import requests
import concurrent.futures

BASE_URL = "http://127.0.0.1:8081"

user_ids = []
for i in range(10):
    resp = requests.post(f"{BASE_URL}/users", json={"username": f"client_{i}"})
    user_ids.append(resp.json()["user_id"])

chat_resp = requests.post(f"{BASE_URL}/chats", json={"name": "Race Chat", "members": user_ids})
chat_id = chat_resp.json()["chat_id"]

def send_msg(user_id, msg_num):
    requests.post(
        f"{BASE_URL}/chats/{chat_id}/messages",
        json={"text": f"race_msg_{msg_num}"},
        headers={"X-User-Id": user_id}
    )

print("Отправка 100 сообщений параллельно...")
with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
    futures = [executor.submit(send_msg, user_ids[i % 10], i) for i in range(100)]
    concurrent.futures.wait(futures)

get_resp = requests.get(f"{BASE_URL}/chats/{chat_id}/messages?limit=100", headers={"X-User-Id": user_ids[0]})
messages = get_resp.json().get("messages", [])

print(f"✅ Отправлено: 100. Получено уникальных: {len(messages)}")
assert len(messages) == 100, f"Ожидалось 100, получено {len(messages)}"
print("🏆 Тест на гонки пройден успешно! Состояние не повреждено.")