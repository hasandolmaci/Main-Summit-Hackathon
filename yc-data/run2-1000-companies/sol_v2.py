"""GPT-6.1-Sol research via OpenAI Responses API with a hard dollar budget.
Reads OPENAI_API_KEY from the environment only.
Usage: python -X utf8 sol_v2.py <folder> <budget_usd> [workers]
Input: <folder>/sol_queue.json (list of batches, each a list of company dicts incl. founders)
Output: <folder>/sol_out/<batch_id>.jsonl, cost log in <folder>/sol_cost.json
"""
import json, os, re, sys, time, threading, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

folder, BUDGET = sys.argv[1], float(sys.argv[2])
WORKERS = int(sys.argv[3]) if len(sys.argv) > 3 else 4
PRICE_IN, PRICE_OUT, PRICE_SEARCH = 2.00 / 1e6, 10.00 / 1e6, 10.00 / 1000
key = os.environ.get("OPENAI_API_KEY") or sys.exit("OPENAI_API_KEY is not set")
brief = open(os.path.join(folder, "brief_sol.md"), encoding="utf-8").read()
queue = json.load(open(os.path.join(folder, "sol_queue.json"), encoding="utf-8"))
os.makedirs(os.path.join(folder, "sol_out"), exist_ok=True)
cost_path = os.path.join(folder, "sol_cost.json")
state = json.load(open(cost_path)) if os.path.exists(cost_path) else {"usd": 0.0, "in": 0, "out": 0, "searches": 0, "batches": 0}
lock = threading.Lock()
EST_PER_BATCH = 1.20  # reserve so that running batches cannot push us over budget

def call(batch):
    body = {"model": "gpt-6.1-sol", "reasoning": {"effort": "medium"}, "tools": [{"type": "web_search", "search_context_size": "low"}],
            "input": brief + "\n\nCompanies (JSON):\n" + json.dumps(batch, ensure_ascii=False) + "\n\nReturn ONLY the JSONL lines."}
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=1200) as r:
        return json.load(r)

reserved = [0.0]
def work(item):
    bid, batch = item
    out = os.path.join(folder, "sol_out", bid + ".jsonl")
    if os.path.exists(out):
        return
    with lock:
        if state["usd"] + reserved[0] + EST_PER_BATCH > BUDGET:
            return
        reserved[0] += EST_PER_BATCH
    try:
        for attempt in range(10):
            try:
                resp = call(batch); break
            except urllib.error.HTTPError as e:
                msg = e.read().decode()
                if e.code in (401, 403, 404):
                    print(bid, "HTTP", e.code, msg[:200], flush=True); os._exit(1)
                m = re.search(r"try again in ([0-9.]+)(ms|s)", msg)
                wait = (float(m.group(1)) / (1000 if m.group(2) == "ms" else 1) + 2) if m else 20 + 10 * attempt
                print(bid, "HTTP", e.code, f"retry in {wait:.0f}s", flush=True)
                time.sleep(wait)
            except Exception as e:
                print(bid, "error", str(e)[:100], flush=True); time.sleep(20)
        else:
            return
        u = resp.get("usage", {})
        s = sum(1 for o in resp.get("output", []) if o.get("type") == "web_search_call")
        c = u.get("input_tokens", 0) * PRICE_IN + u.get("output_tokens", 0) * PRICE_OUT + s * PRICE_SEARCH
        text = "".join(x.get("text", "") for o in resp.get("output", []) if o.get("type") == "message" for x in o.get("content", []))
        lines = [l.strip() for l in text.splitlines() if l.strip().startswith("{")]
        open(out, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        with lock:
            state["usd"] += c; state["in"] += u.get("input_tokens", 0); state["out"] += u.get("output_tokens", 0)
            state["searches"] += s; state["batches"] += 1
            json.dump(state, open(cost_path, "w"))
            print(f"{bid}: {len(lines)} rows, ${c:.2f} | total ${state['usd']:.2f} / ${BUDGET:.2f} | batches {state['batches']}", flush=True)
    finally:
        with lock:
            reserved[0] -= EST_PER_BATCH

with ThreadPoolExecutor(WORKERS) as ex:
    list(ex.map(work, [(f"q{i:03d}", b) for i, b in enumerate(queue)]))
print("FINAL", json.dumps(state))
