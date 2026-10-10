import json
import urllib.request
import time
import sys

URL = "http://127.0.0.1:11434/api/chat"
MODEL = "qwen2.5:3b"

SYSTEM_PROMPT = """Anda adalah ENKI, asisten AI pribadi lokal yang cerdas, efisien, dan siap membantu pengguna dalam pekerjaan komputasi, pengembangan perangkat lunak, dan analisis finansial.
Anda dilengkapi dengan serangkaian tools khusus untuk mengakses memori, membaca layar, mengelola file, menjelajahi web, dan mengeksekusi perintah shell.

PANDUAN PEMANGGILAN TOOL:
1. Jika pengguna meminta mencari riwayat obrolan masa lalu, gunakan tool `search_memory`.
2. Jika pengguna meminta membaca apa yang sedang aktif atau terjadi di layar/terminal, gunakan tool `screen_inspect`.
3. Jika pengguna memberikan URL artikel/web dan meminta isi atau rangkumannya, gunakan tool `web_fetch`.
4. Jika pengguna meminta membuka situs atau link web di peramban (browser), gunakan tool `open_url`.
5. Jika pengguna meminta membaca file lokal dari workspace, gunakan tool `read_file`.
6. Jika pengguna meminta mengeksekusi perintah terminal/shell, gunakan tool `run_shell_command`.
7. UNTUK PERTANYAAN UMUM, SALAM, DISKUSI KONSEPTUAL, PENJELASAN TEORI, ATAU OBROLAN BIASA: JANGAN panggil tool apa pun. Jawablah langsung dalam teks biasa secara ramah dan profesional.
8. Pastikan argumen yang diberikan pada pemanggilan tool selalu lengkap, valid, dan sesuai kebutuhan pengguna."""

DUMMY_PROFILE = """[PROFIL PENGGUNA TERVERIFIKASI - ENKI]
- Nama/Panggilan: User
- Bidang Pekerjaan: Praktisi dan edukator ilmu komputer, pengembang software, dan analis pasar modal/keuangan.
- Tech Stack Utama: Python 3.11+ (FastAPI, PyTorch, Pandas), PostgreSQL, SQLite, Git, Docker, Windows 11 & Linux.
- Proyek Aktif:
  1. Pengembangan sistem asisten AI lokal mandiri bernama ENKI.
  2. Analisis kuantitatif data laporan keuangan emiten dan pergerakan harga saham.
  3. Modul pembelajaran pemrograman backend berbasis API modern.
- Preferensi Kerja & Komunikasi:
  * Gaya komunikasi: Ringkas, jelas, langsung ke inti permasalahan (to-the-point), profesional namun santai.
  * Bahasa utama: Bahasa Indonesia (istilah teknis dapat menggunakan Bahasa Inggris standar industri).
  * Batasan hardware: Bekerja pada mesin dengan GPU RTX 3050 4GB VRAM, utamakan efisiensi memori dan komputasi.
  * Keamanan: Jangan pernah memodifikasi file di luar workspace atau mengeksekusi perintah destruktif tanpa konfirmasi.
  * Kode program: Selalu sertakan tipe data (type hints), penanganan error eksplisit, dan struktur modular."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Cari riwayat percakapan atau informasi masa lalu dalam database memori (FTS5).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Kata kunci pencarian riwayat obrolan"}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "screen_inspect",
            "description": "Ambil teks yang sedang tampil di layar atau window aktif menggunakan OCR.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string", "enum": ["active_window", "full_screen"], "description": "Area tangkapan layar"}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Ambil dan ekstrak konten teks utama dari sebuah halaman web atau artikel URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL halaman web yang akan diekstrak"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Buka alamat web URL di browser default pengguna.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL yang akan dibuka di browser"}
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Baca isi file teks di dalam direktori kerja/workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path relatif file yang akan dibaca"}
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Jalankan perintah shell/terminal aman di lingkungan lokal pengguna.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Perintah shell yang akan dijalankan"}
                },
                "required": ["command"]
            }
        }
    }
]

TEST_PROMPTS = [
    # 9 Prompts Negatif (Tidak butuh tool)
    {"id": 1, "type": "negative", "prompt": "Halo ENKI, selamat pagi! Gimana kabarmu hari ini?", "expected_tool": None},
    {"id": 2, "type": "negative", "prompt": "Jelaskan perbedaan mendasar antara concurrency dan parallelism dalam komputasi.", "expected_tool": None},
    {"id": 3, "type": "negative", "prompt": "Menurutmu, apa kelebihan kuantisasi model Q4 dibanding FP16 pada kartu grafis dengan VRAM terbatas?", "expected_tool": None},
    {"id": 4, "type": "negative", "prompt": "Bisa buatkan analogi sederhana untuk menjelaskan konsep Object-Oriented Programming (OOP) kepada mahasiswa baru?", "expected_tool": None},
    {"id": 5, "type": "negative", "prompt": "Siapa nama kamu dan apa peran utamamu sebagai asisten pribadi lokal?", "expected_tool": None},
    {"id": 6, "type": "negative", "prompt": "Terima kasih banyak atas penjelasannya tadi, sangat membantu dan mudah dipahami!", "expected_tool": None},
    {"id": 7, "type": "negative", "prompt": "Tuliskan sebuah puisi pendek 4 baris tentang programmer yang sedang melakukan debugging di malam hari.", "expected_tool": None},
    {"id": 8, "type": "negative", "prompt": "Bagaimana cara kerja dasar dari mekanisme self-attention pada model Transformer?", "expected_tool": None},
    {"id": 9, "type": "negative", "prompt": "Menurut analisismu, apa keunggulan bahasa Rust dibandingkan Go untuk pembuatan tool sistem CLI?", "expected_tool": None},

    # 4 Prompts search_memory
    {"id": 10, "type": "search_memory", "prompt": "Kemarin kita sempat membahas solusi error waktu kompilasi torch di Windows, kamu ingat solusinya apa?", "expected_tool": "search_memory", "arg_check": lambda a: bool(a.get("query"))},
    {"id": 11, "type": "search_memory", "prompt": "Coba cari di catatan obrolan lama kita, apa nama framework web Python yang pernah saya sebutkan?", "expected_tool": "search_memory", "arg_check": lambda a: bool(a.get("query"))},
    {"id": 12, "type": "search_memory", "prompt": "Pernah nggak saya menyebutkan deadline untuk proyek analisis data saham saya di riwayat chat sebelumnya?", "expected_tool": "search_memory", "arg_check": lambda a: bool(a.get("query"))},
    {"id": 13, "type": "search_memory", "prompt": "Please search our previous conversation logs for the SQLite database configuration settings.", "expected_tool": "search_memory", "arg_check": lambda a: bool(a.get("query"))},

    # 4 Prompts screen_inspect
    {"id": 14, "type": "screen_inspect", "prompt": "Tolong lihat layar saya sekarang, apa pesan error merah yang muncul di terminal?", "expected_tool": "screen_inspect", "arg_check": lambda a: True},
    {"id": 15, "type": "screen_inspect", "prompt": "Bisa tolong periksa apa yang sedang terbuka di window aktif komputer saya saat ini?", "expected_tool": "screen_inspect", "arg_check": lambda a: True},
    {"id": 16, "type": "screen_inspect", "prompt": "Cek terminal VS Code saya di layar, apakah proses build tadi sudah selesai atau masih jalan?", "expected_tool": "screen_inspect", "arg_check": lambda a: True},
    {"id": 17, "type": "screen_inspect", "prompt": "Inspect what is currently displayed on my active desktop window right now.", "expected_tool": "screen_inspect", "arg_check": lambda a: True},

    # 4 Prompts web_fetch
    {"id": 18, "type": "web_fetch", "prompt": "Tolong ambil dan rangkum isi artikel dokumentasi di https://fastapi.tiangolo.com/tutorial/first-steps/", "expected_tool": "web_fetch", "arg_check": lambda a: "http" in a.get("url", "").lower()},
    {"id": 19, "type": "web_fetch", "prompt": "Ekstrak teks utama dari artikel berita teknologi di link https://news.ycombinator.com/item?id=38000000", "expected_tool": "web_fetch", "arg_check": lambda a: "http" in a.get("url", "").lower()},
    {"id": 20, "type": "web_fetch", "prompt": "Ambilkan konten teks dari dokumentasi Python di https://docs.python.org/3/library/sqlite3.html", "expected_tool": "web_fetch", "arg_check": lambda a: "http" in a.get("url", "").lower()},
    {"id": 21, "type": "web_fetch", "prompt": "Please fetch the content of this webpage: https://en.wikipedia.org/wiki/Artificial_intelligence", "expected_tool": "web_fetch", "arg_check": lambda a: "http" in a.get("url", "").lower()},

    # 3 Prompts open_url
    {"id": 22, "type": "open_url", "prompt": "Buka Google di web browser saya dong.", "expected_tool": "open_url", "arg_check": lambda a: "google" in a.get("url", "").lower()},
    {"id": 23, "type": "open_url", "prompt": "Tolong bukakan link repository https://github.com/ollama/ollama di browser default komputer.", "expected_tool": "open_url", "arg_check": lambda a: "github" in a.get("url", "").lower() or "http" in a.get("url", "").lower()},
    {"id": 24, "type": "open_url", "prompt": "Buka situs YouTube di browser.", "expected_tool": "open_url", "arg_check": lambda a: "youtube" in a.get("url", "").lower()},

    # 3 Prompts read_file
    {"id": 25, "type": "read_file", "prompt": "Tolong baca isi file config.json yang ada di folder proyek.", "expected_tool": "read_file", "arg_check": lambda a: "config.json" in a.get("file_path", "").lower()},
    {"id": 26, "type": "read_file", "prompt": "Bisa periksa apa saja dependency yang tertulis di dalam file requirements.txt?", "expected_tool": "read_file", "arg_check": lambda a: "requirements.txt" in a.get("file_path", "").lower()},
    {"id": 27, "type": "read_file", "prompt": "Read the contents of the file notes.md in my workspace.", "expected_tool": "read_file", "arg_check": lambda a: "notes.md" in a.get("file_path", "").lower()},

    # 3 Prompts run_shell_command
    {"id": 28, "type": "run_shell_command", "prompt": "Jalankan perintah git status di terminal untuk melihat status perubahan file.", "expected_tool": "run_shell_command", "arg_check": lambda a: "git status" in a.get("command", "").lower()},
    {"id": 29, "type": "run_shell_command", "prompt": "Cek versi Python yang sedang aktif dengan menjalankan command python --version.", "expected_tool": "run_shell_command", "arg_check": lambda a: "python" in a.get("command", "").lower()},
    {"id": 30, "type": "run_shell_command", "prompt": "Jalankan perintah dir untuk melihat daftar file dan folder di direktori saat ini.", "expected_tool": "run_shell_command", "arg_check": lambda a: "dir" in a.get("command", "").lower()}
]

def call_ollama(prompt):
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": f"{SYSTEM_PROMPT}\n\n{DUMMY_PROFILE}"},
            {"role": "user", "content": prompt}
        ],
        "tools": TOOLS,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": 8192
        }
    }

    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def evaluate_call(test_case, response):
    message = response.get("message", {})
    tool_calls = message.get("tool_calls")
    expected = test_case["expected_tool"]

    # Negative prompt
    if expected is None:
        if not tool_calls:
            return {"status": "SUCCESS", "detail": "Correctly responded with text, no tool called"}
        else:
            called = [tc.get("function", {}).get("name") for tc in tool_calls]
            return {"status": "FALSE_POSITIVE", "detail": f"Wrongly called tool(s): {called}"}

    # Positive prompt
    if not tool_calls:
        return {"status": "MISSED_TOOL", "detail": f"Expected {expected}, but returned text without tool call"}

    first_call = tool_calls[0].get("function", {})
    actual_name = first_call.get("name")
    actual_args = first_call.get("arguments", {})

    if actual_name != expected:
        return {"status": "WRONG_TOOL", "detail": f"Expected {expected}, but called {actual_name}"}

    # Check arguments
    arg_check = test_case.get("arg_check")
    if arg_check:
        try:
            if arg_check(actual_args):
                return {"status": "SUCCESS", "detail": f"Called {actual_name} with valid args: {actual_args}"}
            else:
                return {"status": "INVALID_ARGUMENTS", "detail": f"Called {actual_name} with invalid/malformed args: {actual_args}"}
        except Exception as e:
            return {"status": "INVALID_ARGUMENTS", "detail": f"Error validating args {actual_args}: {e}"}

    return {"status": "SUCCESS", "detail": f"Called {actual_name} with args: {actual_args}"}

def run_benchmark():
    print(f"Starting Benchmark: 30 prompts x 3 runs = 90 test calls...")
    print(f"Model: {MODEL} | num_ctx: 8192 | temperature: 0.2\n")

    results = []
    
    total_calls = 0
    total_success = 0
    total_false_positive = 0
    total_invalid_args = 0
    total_wrong_tool = 0
    total_missed_tool = 0

    for idx, item in enumerate(TEST_PROMPTS):
        p_id = item["id"]
        prompt = item["prompt"]
        p_type = item["type"]
        expected = item["expected_tool"]

        prompt_runs = []
        prompt_success_count = 0

        print(f"[{idx+1}/30] ID {p_id} ({p_type}): '{prompt[:45]}...'")

        for r in range(3):
            total_calls += 1
            t0 = time.time()
            try:
                resp = call_ollama(prompt)
                eval_res = evaluate_call(item, resp)
            except Exception as e:
                eval_res = {"status": "ERROR", "detail": str(e)}

            status = eval_res["status"]
            detail = eval_res["detail"]
            duration = round(time.time() - t0, 2)

            if status == "SUCCESS":
                total_success += 1
                prompt_success_count += 1
            elif status == "FALSE_POSITIVE":
                total_false_positive += 1
            elif status == "INVALID_ARGUMENTS":
                total_invalid_args += 1
            elif status == "WRONG_TOOL":
                total_wrong_tool += 1
            elif status == "MISSED_TOOL":
                total_missed_tool += 1

            prompt_runs.append({
                "run": r + 1,
                "status": status,
                "detail": detail,
                "duration_sec": duration
            })
            print(f"   Run {r+1}: {status} ({duration}s) - {detail}")

        accuracy = f"{prompt_success_count}/3"
        results.append({
            "id": p_id,
            "type": p_type,
            "prompt": prompt,
            "expected_tool": expected,
            "accuracy": accuracy,
            "success_rate": round(prompt_success_count / 3 * 100, 1),
            "runs": prompt_runs
        })
        print(f"   Accuracy: {accuracy}\n")

    overall_accuracy = round(total_success / total_calls * 100, 2)
    fp_rate = round(total_false_positive / total_calls * 100, 2)
    inv_arg_rate = round(total_invalid_args / total_calls * 100, 2)
    wrong_or_missed = round((total_wrong_tool + total_missed_tool) / total_calls * 100, 2)

    summary = {
        "total_calls": total_calls,
        "total_success": total_success,
        "overall_accuracy_percent": overall_accuracy,
        "total_false_positive": total_false_positive,
        "false_positive_percent": fp_rate,
        "total_invalid_args": total_invalid_args,
        "invalid_args_percent": inv_arg_rate,
        "total_wrong_or_missed": total_wrong_tool + total_missed_tool,
        "wrong_or_missed_percent": wrong_or_missed,
        "results_by_prompt": results
    }

    with open("D:/ENKI/ENKI/_spike/benchmark_results.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print("\n" + "="*50)
    print("BENCHMARK SUMMARY")
    print("="*50)
    print(f"Total Calls             : {total_calls}")
    print(f"Sukses (True Pos + Neg) : {total_success} ({overall_accuracy}%)")
    print(f"Salah-Panggil (Negatif) : {total_false_positive} ({fp_rate}%)")
    print(f"Argumen Salah           : {total_invalid_args} ({inv_arg_rate}%)")
    print(f"Salah/Missed Tool       : {total_wrong_tool + total_missed_tool} ({wrong_or_missed}%)")
    print("="*50)
    if overall_accuracy >= 80.0:
        print("RESULT: PASSED (>= 80%)")
    else:
        print("RESULT: FAILED (< 80%) - STOP REQUIRED")
    print("="*50)

if __name__ == "__main__":
    run_benchmark()
