"""
Skrip pengukuran status runtime Ollama dan VRAM GPU:
1. Status model yang terpasang (ollama list)
2. Pengukuran model qwen2.5:3b (ollama ps & nvidia-smi)
3. Pengukuran model qwen2.5-coder:3b (ollama ps & nvidia-smi)
4. Pengukuran model nomic-embed-text:latest (ollama ps & nvidia-smi)
5. Verifikasi baris 'server config' dari log proses Ollama
Menyimpan hasil ke docs/evidence/ollama_measure.txt.
"""

import subprocess
import time
import urllib.request
import json
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_FILE = ROOT_DIR / "docs" / "evidence" / "ollama_measure.txt"

def run_cmd(args):
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=10)
        return (res.stdout.strip() or res.stderr.strip())
    except Exception as e:
        return f"Error: {e}"

def warm_model(model_name: str, num_ctx: int = 4096):
    url = "http://127.0.0.1:11434/api/chat"
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
        "options": {"num_ctx": num_ctx, "num_predict": 2}
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except Exception as e:
        print(f"Warning warming {model_name}: {e}")

def get_nvidia_smi():
    return run_cmd(["nvidia-smi", "--query-gpu=name,memory.total,memory.used,memory.free", "--format=csv,noheader"])

def main():
    lines = []
    lines.append("=== 1. OLLAMA LIST ===")
    lines.append(run_cmd(["ollama", "list"]))
    lines.append("")

    # Chat model
    lines.append("=== 2. LANGKAH 1: CHAT (qwen2.5:3b) ===")
    warm_model("qwen2.5:3b", num_ctx=8192)
    time.sleep(1)
    lines.append("[OLLAMA PS]")
    lines.append(run_cmd(["ollama", "ps"]))
    lines.append("")
    lines.append("[NVIDIA-SMI]")
    lines.append(get_nvidia_smi())
    lines.append("")

    # Coder model
    lines.append("=== 3. LANGKAH 2: CODER (qwen2.5-coder:3b) ===")
    warm_model("qwen2.5-coder:3b", num_ctx=4096)
    time.sleep(1)
    lines.append("[OLLAMA PS]")
    lines.append(run_cmd(["ollama", "ps"]))
    lines.append("")
    lines.append("[NVIDIA-SMI]")
    lines.append(get_nvidia_smi())
    lines.append("")

    # Nomic embedding
    lines.append("=== 4. LANGKAH 3: NOMIC EMBEDDING (nomic-embed-text:latest) ===")
    # warm embedding
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/embeddings",
        data=json.dumps({"model": "nomic-embed-text:latest", "prompt": "test"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except Exception as e:
        print(f"Warning warming nomic: {e}")
    time.sleep(1)
    lines.append("[OLLAMA PS]")
    lines.append(run_cmd(["ollama", "ps"]))
    lines.append("")
    lines.append("[NVIDIA-SMI]")
    lines.append(get_nvidia_smi())
    lines.append("")

    # Kembali ke model chat agar VRAM kembali siap untuk benchmark/smoke
    warm_model("qwen2.5:3b", num_ctx=8192)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    server_config_part = ""
    if OUTPUT_FILE.exists():
        old_text = OUTPUT_FILE.read_text(encoding="utf-8")
        if "=== 5. BARIS 'SERVER CONFIG' TERAKHIR ===" in old_text:
            server_config_part = old_text[old_text.find("=== 5. BARIS 'SERVER CONFIG' TERAKHIR ==="):]

    content = "\n".join(lines).strip()
    if server_config_part:
        content += "\n\n" + server_config_part.strip() + "\n"
    else:
        content += "\n"

    OUTPUT_FILE.write_text(content, encoding="utf-8")
    print(f"Pengukuran selesai. Berkas disimpan di: {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
