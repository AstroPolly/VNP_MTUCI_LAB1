#!/usr/bin/env python3
"""Small closed-loop HTTP write benchmark; not an open-loop capacity proof."""
from concurrent.futures import ThreadPoolExecutor
import json
import math
from pathlib import Path
import threading
import time
from client import Client, Failure, LAB, arguments, check, run


def main():
    def configure(p):
        p.add_argument('--duration',type=float,default=30)
        p.add_argument('--concurrency',type=int,default=4)
        p.add_argument('--output',default=None,help='Optional JSON metrics, no session tokens')
    args = arguments('Synthetic POST message load, closed loop; creates users/messages',configure)
    check(1<=args.duration<=3600,'duration must be 1..3600 seconds')
    check(1<=args.concurrency<=128,'concurrency must be 1..128')
    c = Client(args.base_url,args.ca_file)
    alice,bob,eve,chat = c.bootstrap()
    barrier = threading.Barrier(args.concurrency)
    def worker(worker_id):
        local = Client(args.base_url,args.ca_file,timeout=5)
        barrier.wait()
        deadline = time.monotonic()+args.duration
        latencies,errors = [],0
        index = 0
        while time.monotonic()<deadline:
            start = time.monotonic()
            try:
                local.message(alice,chat,'load-%s-%s' % (worker_id,index))
                latencies.append(time.monotonic()-start)
            except Failure:
                errors += 1
            index += 1
        return latencies,errors
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        results = list(pool.map(worker,range(args.concurrency)))
    elapsed = time.monotonic()-started
    latencies = sorted(x for values,_ in results for x in values)
    errors = sum(n for _,n in results)
    total = len(latencies)+errors
    def percentile(p):
        return round(1000*latencies[max(0,math.ceil(p*len(latencies))-1)],3) if latencies else None
    report = {'lab':LAB,'workload':'closed-loop POST messages; no automatic retries','requested_duration_seconds':args.duration,'elapsed_seconds':round(elapsed,3),'concurrency':args.concurrency,'successful_requests':len(latencies),'failed_requests':errors,'error_ratio':round(errors/total,6) if total else None,'successful_rps':round(len(latencies)/elapsed,3),'successful_latency_ms':{'p50':percentile(.50),'p95':percentile(.95),'p99':percentile(.99)},'note':'Latency percentiles cover successes only. Timeouts/errors are counted separately; closed-loop has coordinated omission and client overhead.'}
    text = json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:
        dest = Path(args.output)
        dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(text+'\n')
    print(text)
    check(bool(latencies),'no successful writes')
    # Errors are reported, not treated as a universal load-test threshold.

if __name__=='__main__':
    run(main)
