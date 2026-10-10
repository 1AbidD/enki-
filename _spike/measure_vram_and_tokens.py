import json
import urllib.request
import subprocess
import time

def get_vram():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.free,memory.total", "--format=csv,noheader,nounits"],
            text=True
        ).strip()
        used, free, total = [int(x.strip()) for x in out.split(",")]
        return {"used_mib": used, "free_mib": free, "total_mib": total}
    except Exception as e:
        return {"error": str(e)}

def run_test(num_ctx, prompt_text):
    # Unload first to reset KV cache
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/generate",
            data=json.dumps({"model": "qwen2.5:3b", "keep_alive": 0}).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        urllib.request.urlopen(req)
        time.sleep(2)
    except Exception:
        pass

    vram_before = get_vram()

    payload = {
        "model": "qwen2.5:3b",
        "messages": [
            {"role": "user", "content": prompt_text}
        ],
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "temperature": 0.2
        }
    }

    t0 = time.time()
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    elapsed = time.time() - t0

    vram_after = get_vram()

    prompt_eval_count = res.get("prompt_eval_count", 0)
    prompt_eval_duration = res.get("prompt_eval_duration", 1) / 1e9
    eval_count = res.get("eval_count", 0)
    eval_duration = res.get("eval_duration", 1) / 1e9

    tok_per_sec = eval_count / eval_duration if eval_duration > 0 else 0
    prompt_tok_per_sec = prompt_eval_count / prompt_eval_duration if prompt_eval_duration > 0 else 0

    return {
        "num_ctx": num_ctx,
        "vram_before": vram_before,
        "vram_after": vram_after,
        "prompt_eval_count": prompt_eval_count,
        "prompt_eval_duration_sec": round(prompt_eval_duration, 4),
        "prompt_tok_per_sec": round(prompt_tok_per_sec, 2),
        "eval_count": eval_count,
        "eval_duration_sec": round(eval_duration, 4),
        "eval_tok_per_sec": round(tok_per_sec, 2),
        "total_elapsed_sec": round(elapsed, 2)
    }

if __name__ == "__main__":
    # Context payload with realistic text size (~1200 words / ~1600 tokens)
    filler_text = ("Berikut adalah data konteks teknis sistem ENKI. " * 150) + "Tolong jelaskan secara singkat arsitektur sistem ENKI dalam 3 kalimat."

    print("Measuring num_ctx = 4096...")
    res_4096 = run_test(4096, filler_text)
    print("RES 4096:", json.dumps(res_4096, indent=2))

    print("\nMeasuring num_ctx = 8192...")
    # Longer text (~2500 words / ~3500 tokens)
    filler_text_long = ("Berikut adalah data konteks teknis sistem ENKI dan konfigurasi database. " * 300) + "Tolong jelaskan secara singkat arsitektur sistem ENKI dalam 3 kalimat."
    res_8192 = run_test(8192, filler_text_long)
    print("RES 8192:", json.dumps(res_8192, indent=2))
