import requests
import concurrent.futures
import os

# Исправлен адрес: теперь 8080 (внутри контейнера) или из переменной окружения
BASE_URL = os.getenv("API_URL", "http://127.0.0.1:8080/api/v1")

def test_negative_cases():
    print("Тест: Дубликат username (ожидается 409)")
    requests.post(f"{BASE_URL}/users", json={"username": "testuser1"})
    r2 = requests.post(f"{BASE_URL}/users", json={"username": "testuser1"})
    assert r2.status_code == 409, f"Ожидался 409, получен {r2.status_code}"

    print("Тест: Некорректный username (ожидается 400)")
    r3 = requests.post(f"{BASE_URL}/users", json={"username": "BAD NAME!"})
    assert r3.status_code == 400

    print("Тест: Повреждённый JSON (ожидается 400)")
    r4 = requests.post(f"{BASE_URL}/users", data="{bad json}", headers={"Content-Type": "application/json"})
    assert r4.status_code == 400

    print("Тест: Отсутствие X-User-Id (ожидается 401)")
    r5 = requests.get(f"{BASE_URL}/chats")
    assert r5.status_code == 401

    print("Тест: Чтение чужого чата (ожидается 404)")
    u1 = requests.post(f"{BASE_URL}/users", json={"username": "user_a"}).json()
    u2 = requests.post(f"{BASE_URL}/users", json={"username": "user_b"}).json()
    chat = requests.post(f"{BASE_URL}/chats", json={"title": "Test", "member_ids": [u1["user_id"], u2["user_id"]]}, headers={"X-User-Id": u1["user_id"]}).json()
    u3 = requests.post(f"{BASE_URL}/users", json={"username": "user_c"}).json()
    r7 = requests.get(f"{BASE_URL}/chats/{chat['chat_id']}", headers={"X-User-Id": u3["user_id"]})
    assert r7.status_code == 404

    print("Тест: Некорректный cursor (ожидается 400)")
    r8 = requests.get(f"{BASE_URL}/chats/{chat['chat_id']}/messages?cursor=invalid", headers={"X-User-Id": u1["user_id"]})
    assert r8.status_code == 400
    print("Все негативные тесты пройдены!\n")

def test_concurrency():
    print("Тест: Конкурентная запись (100 сообщений от 10 клиентов)")
    user_ids = [requests.post(f"{BASE_URL}/users", json={"username": f"client_{i}_{os.urandom(4).hex()}"}).json()["user_id"] for i in range(10)]
    chat_id = requests.post(f"{BASE_URL}/chats", json={"title": "Race Chat", "member_ids": user_ids}, headers={"X-User-Id": user_ids[0]}).json()["chat_id"]

    sent_ids = []
    def send_msg(user_id, msg_num):
        resp = requests.post(f"{BASE_URL}/chats/{chat_id}/messages", json={"text": f"race_msg_{msg_num}"}, headers={"X-User-Id": user_id})
        if resp.status_code == 201:
            sent_ids.append(resp.json()["message_id"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        concurrent.futures.wait([executor.submit(send_msg, user_ids[i % 10], i) for i in range(100)])

    messages = requests.get(f"{BASE_URL}/chats/{chat_id}/messages?limit=100", headers={"X-User-Id": user_ids[0]}).json().get("items", [])
    received_ids = [msg["message_id"] for msg in messages]

    assert len(sent_ids) == 100, f"Ожидалось 100 подтверждений, получено {len(sent_ids)}"
    assert len(set(received_ids)) == 100, f"Ожидалось 100 уникальных сообщений, получено {len(set(received_ids))}"
    print(f"Отправлено и подтверждено: {len(sent_ids)}. Уникальных получено: {len(set(received_ids))}")
    print("Тест на гонки пройден успешно!\n")

if __name__ == "__main__":
    test_negative_cases()
    test_concurrency()
    print("Все тесты успешно завершены")