import requests
import matplotlib.pyplot as plt
import os

BASE_URL = "http://127.0.0.1:8081"


user_resp = requests.post(f"{BASE_URL}/users", json={"username": "degradation_tester"})
user_id = user_resp.json()["user_id"]
chat_resp = requests.post(f"{BASE_URL}/chats", json={"name": "Degradation Chat", "members": [user_id]})
chat_id = chat_resp.json()["chat_id"]
headers = {"X-User-Id": user_id}

status_codes = []
print("Отправка 600 запросов (лимит сервера = 500)...")
for i in range(600):
    try:
        response = requests.post(f"{BASE_URL}/chats/{chat_id}/messages", json={"text": f"msg_{i}"}, headers=headers)
        status_codes.append(response.status_code)
    except requests.exceptions.RequestException:
        status_codes.append(500)

print("Построение ТОЧЕЧНОГО графика (scatter)...")
os.makedirs("evidence", exist_ok=True)

plt.figure(figsize=(12, 6))


x = list(range(len(status_codes)))
y = status_codes


colors = ['green' if code == 200 else 'red' for code in y]


plt.scatter(x, y, c=colors, s=30, alpha=0.8, edgecolors='none', label='Результаты запросов')


plt.axhline(y=503, color='darkred', linestyle='--', linewidth=2, alpha=0.7, label='Лимит (503)')
plt.axhline(y=200, color='darkgreen', linestyle='--', linewidth=2, alpha=0.7, label='Успех (200)')

plt.title("Трек 2: Управляемая деградация\n(Первые 500 успешны, далее сервер защищает память от OOM)", fontsize=14, fontweight='bold')
plt.xlabel("Номер запроса (время)", fontsize=12)
plt.ylabel("HTTP Status Code", fontsize=12)

# Делаем подписи на оси Y понятными
plt.yticks([200, 503], ["200 OK (Успех)", "503 Service Unavailable (Отказ)"], fontsize=11)

plt.legend(loc='center right', fontsize=11)
plt.grid(True, axis='x', alpha=0.3) # Сетка только по X, чтобы не мешать точкам

plt.tight_layout()
plt.savefig("evidence/graph_track2.png", dpi=300, bbox_inches='tight')
print("🎨 Идеальный точечный график сохранен: evidence/graph_track2.png")