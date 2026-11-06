"""Isolated real-process experiments: server RSS, recovery and storage capacity."""
import concurrent.futures as futures
import csv
import json
import os
from pathlib import Path
import socket
import signal
import subprocess
import sys
import time
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import psutil
import requests
from test import history
from client import Client, check
ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'evidence'


def launch(label, **settings):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, HOST='127.0.0.1', PORT=str(port))
    env.update({k: str(v) for k, v in settings.items()})
    log = open(OUT / (label + '-server.log'), 'w', encoding='utf-8')
    p = subprocess.Popen([sys.executable, 'run.py'], cwd=ROOT, env=env, stdout=log, stderr=log)
    base = 'http://127.0.0.1:' + str(port)
    for _ in range(100):
        if p.poll() is not None:
            raise RuntimeError('Server exited; inspect log')
        try:
            if requests.get(base + '/health/ready', timeout=.2).status_code == 200:
                return p, log, base
        except requests.RequestException:
            pass
        time.sleep(.05)
    p.terminate()
    p.wait(timeout=10)
    log.close()
    raise RuntimeError('Startup timeout')


def stop(p, log):
    start = time.monotonic()
    p.terminate()
    try:
        p.wait(timeout=10)
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()
        log.close()
    elapsed = time.monotonic() - start
    check(p.returncode in (0, -signal.SIGTERM) and elapsed <= 10, 'unclean shutdown')
    check('Application shutdown complete.' in Path(log.name).read_text(), 'shutdown hook did not finish')
    return elapsed


def experiment(label, capacity=False):
    settings = dict(WRITE_RATE=10000 if capacity else 40, WRITE_BURST=10000 if capacity else 20,
                    MAX_MESSAGES=1000 if capacity else 5000)
    p, log, base = launch(label, **settings)
    rows, confirmed, chats = [], {}, []
    started = time.monotonic()
    try:
        c = Client(base)
        actor = c.user('research')
        for _ in range(6 if capacity else 1):
            chats.append(c.call('POST', '/api/v1/chats', 201, actor=actor,
                body={'title': 'research', 'member_ids': [actor['id']]}, schema='Chat')['json'])
        phases = [('capacity', 80)] * 15 if capacity else [('normal', 5)] * 4 + [('overload', 80)] * 8 + [('recovery', 5)] * 4
        previous_phase = None
        for phase, amount in phases:
            if phase == 'recovery' and previous_phase != phase:
                time.sleep(.6)  # explicit removal of load: let token bucket refill
            previous_phase = phase
            t0 = time.monotonic()
            def send(i):
                r = requests.post(base + '/api/v1/chats/' + chats[i % len(chats)]['id'] + '/messages',
                    headers={'X-User-Id': actor['id']}, json={'text': '🙂' * 4000}, timeout=10)
                check(r.status_code in (201, 429, 503), 'unexpected response ' + str(r.status_code))
                if r.status_code == 201:
                    return r.status_code, r.json()
                check(r.json()['error']['request_id'] == r.headers['X-Request-Id'], 'error correlation')
                return r.status_code, None
            with futures.ThreadPoolExecutor(max_workers=20) as pool:
                responses = list(pool.map(send, range(amount)))
            for code, message in responses:
                if message:
                    check(message['id'] not in confirmed, 'duplicate confirmation')
                    confirmed[message['id']] = message
            time.sleep(max(0, .25 - (time.monotonic() - t0)))
            duration = time.monotonic() - t0
            rows.append({'seconds': time.monotonic() - started, 'phase': phase, 'attempts': amount,
                'requests_per_second': amount / duration, 'accepted': sum(s == 201 for s, _ in responses),
                'rejected_429': sum(s == 429 for s, _ in responses),
                'rejected_503': sum(s == 503 for s, _ in responses),
                'rss_mib': psutil.Process(p.pid).memory_info().rss / 1024**2})
        if not capacity:
            time.sleep(.6)
            recovered = c.message(actor, chats[0], 'after overload')['json']
            confirmed[recovered['id']] = recovered
            check(sum(r['rejected_429'] for r in rows) > 0, 'no overload reached')
            check(all(r['accepted'] == r['attempts'] for r in rows if r['phase'] == 'recovery'), 'did not recover')
        else:
            check(len(confirmed) == 1000, 'global capacity mismatch')
            check(sum(r['rejected_503'] for r in rows) == 200, 'wrong capacity rejections')
        received = [m for chat in chats for m in history(c, actor, chat, 100)]
        check(len(received) == len(confirmed), 'lost or duplicated messages')
        check({m['id']: m for m in received} == confirmed, 'confirmed messages changed')
        c.call('GET', '/health/ready', schema='Health')
        old_id = actor['id']
    finally:
        shutdown = stop(p, log)
    fresh, fresh_log, fresh_base = launch(label + '-restart', **settings)
    try:
        Client(fresh_base).call('GET', '/api/v1/users/me', 401, actor={'id': old_id})
    finally:
        stop(fresh, fresh_log)
    with open(OUT / (label + '.csv'), 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    fig, axes = plt.subplots(3, 1, figsize=(10, 8), sharex=True)
    x = [r['seconds'] for r in rows]
    axes[0].plot(x, [r['requests_per_second'] for r in rows], marker='.')
    axes[0].set_ylabel('Requests / second')
    axes[1].plot(x, [r['rejected_429'] for r in rows], label='429 / batch')
    axes[1].plot(x, [r['rejected_503'] for r in rows], label='503 / batch')
    axes[1].legend()
    axes[1].set_ylabel('Rejected requests')
    axes[2].plot(x, [r['rss_mib'] for r in rows], color='purple')
    axes[2].set_ylabel('SERVER RSS (MiB)')
    axes[2].set_xlabel('Elapsed seconds')
    for ax in axes:
        ax.grid(alpha=.25)
    fig.suptitle(label + ' — isolated server process')
    fig.tight_layout()
    fig.savefig(OUT / (label + '.png'), dpi=150)
    plt.close(fig)
    summary = {'settings': settings, 'attempted': sum(r['attempts'] for r in rows),
        'confirmed_including_recovery_probe': len(confirmed), 'verified_read': len(received),
        'rejected_429': sum(r['rejected_429'] for r in rows), 'rejected_503': sum(r['rejected_503'] for r in rows),
        'rss_min_mib': min(r['rss_mib'] for r in rows), 'rss_max_mib': max(r['rss_mib'] for r in rows),
        'sigterm_seconds': shutdown, 'restart_old_user_status': 401,
        'automatic_recovery_without_loss': not capacity}
    print(label, json.dumps(summary, indent=2), flush=True)
    return summary


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    results = {'runtime': sys.version, 'transient': experiment('transient'),
               'capacity': experiment('capacity', capacity=True)}
    (OUT / 'research-summary.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('PASS: transient recovery, global capacity, integrity, SIGTERM and restart')
