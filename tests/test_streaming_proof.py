import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from enki.core.ollama_client import OllamaClient
from enki.config import AppConfig

def verify_streaming():
    print("=" * 60)
    print("BUKTI PROGRESSIVE TOKEN STREAMING (stream=True)")
    print("=" * 60)

    config = AppConfig()
    client = OllamaClient(base_url=config.ollama_url)

    test_prompt = [{"role": "user", "content": "Tuliskan 3 baris puisi pendek tentang komputer."}]

    print(f"Mengirim prompt uji ke model '{config.model}' dengan streaming aktif...\n")

    start_time = time.time()
    chunks = []
    chunk_times = []
    final_metrics = None

    # Mengonsumsi generator token secara bertahap persis seperti di console.py
    for token, metrics in client.chat_stream(
        model=config.model,
        messages=test_prompt,
        num_ctx=config.num_ctx,
        temperature=0.2
    ):
        elapsed = time.time() - start_time
        if token:
            chunks.append(token)
            chunk_times.append(elapsed)
            # Simulasi output konsol instan bertahap
            print(token, end="", flush=True)
        if metrics:
            final_metrics = metrics

    print("\n\n" + "-" * 60)
    print("BUKTI TELEMETRI STREAMING:")
    print(f"Total Chunk/Token Diterima : {len(chunks)} chunks")
    print(f"Waktu Chunk Pertama (TTFT)  : {round(chunk_times[0], 3)} detik")
    print(f"Waktu Selesai Total        : {round(chunk_times[-1], 3)} detik")
    print(f"Kecepatan Inferensi        : {final_metrics.get('eval_tok_per_sec', 0.0)} tok/s")
    print("-" * 60)

    # Verifikasi bahwa token tidak datang sekaligus (streaming sejati)
    assert len(chunks) > 5, "Harus menerima lebih dari 5 chunk streaming"
    assert chunk_times[-1] > chunk_times[0], "Waktu antar chunk harus bertahap (non-blocking streaming)"
    print("[STATUS] Streaming TERBUKTI aktif, progresif, dan non-blocking!")
    print("=" * 60)

if __name__ == "__main__":
    verify_streaming()
