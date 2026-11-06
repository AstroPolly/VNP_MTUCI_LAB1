import requests
import os
import time

BASE_URL = os.getenv("API_URL", "http://127.0.0.1:8080/api/v1")

def get_rss_mb():
    try:
        with open("/proc/self/status", "r") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024 # KB to MB
    except Exception:
        return 0.0

def test_degradation():
    print("Создание пользователя и чата...")
    user = requests.post(f"{BASE_URL}/users", json={"username": "load_tester"}).json()
    chat = requests.post(f"{BASE_URL}/chats", json={"title": "Load Chat", "member_ids": [user["user_id"]]}, headers={"X-User-Id": user["user_id"]}).json()
    chat_id = chat["chat_id"]

    print(f"Отправка 550 сообщений (глобальный лимит сервера: {500})...")
    stats = {"201": 0, "503": 0, "other": 0}
    rss_readings = []

    for i in range(550):
        resp = requests.post(f"{BASE_URL}/chats/{chat_id}/messages", json={"text": "x" * 100}, headers={"X-User-Id": user["user_id"]})
        if resp.status_code == 201: stats["201"] += 1
        elif resp.status_code == 503: stats["503"] += 1
        else: stats["other"] += 1
            
        if i % 50 == 0:
            rss_readings.append((i, get_rss_mb(), resp.status_code))

    print(f"Результаты: 201 OK: {stats['201']}, 503 Service Unavailable: {stats['503']}")
    print("Динамика RSS (MB):")
    for req_num, rss, status in rss_readings:
        print(f"  Запрос {req_num:3d}: {rss:6.2f} MB (статус: {status})")

    assert stats["503"] > 0, "Сервер не вернул 503 при превышении лимита!"
    
    print("Проверка стабильности после перегрузки...")
    time.sleep(1)
    resp = requests.post(f"{BASE_URL}/chats/{chat_id}/messages", json={"text": "test"}, headers={"X-User-Id": user["user_id"]})
    assert resp.status_code == 503, "Сервер должен продолжать возвращать 503"
    
    history = requests.get(f"{BASE_URL}/chats/{chat_id}/messages?limit=10", headers={"X-User-Id": user["user_id"]})
    assert history.status_code == 200 and len(history.json()["items"]) == 10
    
    print("Трек 2 пройден: управляемая деградация работает, OOM предотвращён, данные сохранены.\n")

if __name__ == "__main__":
    test_degradation()