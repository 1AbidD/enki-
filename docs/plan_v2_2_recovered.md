# Rencana Implementasi: ENKI (Local AI Assistant) - v2.2 (Hasil Spike Tahap 0)

ENKI adalah asisten AI pribadi lokal yang dirancang khusus untuk hardware **NVIDIA RTX 3050 4GB VRAM**.
Tahap 0 (Spike) telah **LULUS** dengan akurasi tool-calling **91.11%** (82/90 sukses) dan jejak VRAM riil **2644 MiB pada context 8192 token** (100% GPU di 67.4 tok/s). Folder `D:\ENKI\ENKI\_spike\` tetap dipertahankan sebagai rangkaian uji regresi (*regression test suite*).

---

## 1. Status Model, Runtime Ollama & Kebijakan Hardware

### Hasil Pengukuran Riil Tahap 0 & Status GPU
* **Versi Ollama:** `v0.33.0`
* **Model Terpasang & Verifikasi `ollama ps`:**
  * `qwen2.5:3b` : Terverifikasi **100% GPU** (Ukuran: 2.2 GB, Context: 4096/8192)
  * `qwen2.5-coder:3b` : Terverifikasi **100% GPU** (Ukuran: 2.2 GB)
  * Saat switch model, `qwen2.5:3b` otomatis ter-eject dari VRAM berkat kebijakan `OLLAMA_MAX_LOADED_MODELS=1`.
* **Angka Mentah VRAM (`nvidia-smi`):**
  * Baseline Idle: **347 MiB** (dari 4096 MiB total)
  * Inferensi `num_ctx = 4096`: **2496 MiB** (Sisa free: 1467 MiB) @ 70.97 tok/s
  * Inferensi `num_ctx = 8192`: **2644 MiB** (Sisa free: 1319 MiB) @ 67.43 tok/s
* **Pengukuran Tambahan (Tahap 1):** Mengukur ulang VRAM saat VS Code dan Browser (Chrome/Edge) sedang terbuka aktif untuk melihat sisa headroom VRAM harian.
* **Pengukuran Swap Model (Tahap 3):** Mencatat waktu latensi swap `/coder` (unload 3B chat -> load 3B coder).

### Cara Set Environment Variable Ollama Secara Permanen di Windows
Agar konfigurasi tidak hilang saat restart atau buka terminal baru:
1. **Via PowerShell (Run as Administrator):**
   ```powershell
   [System.Environment]::SetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS', '1', 'Machine')
   [System.Environment]::SetEnvironmentVariable('OLLAMA_NUM_PARALLEL', '1', 'Machine')
   [System.Environment]::SetEnvironmentVariable('OLLAMA_FLASH_ATTENTION', '1', 'Machine')
   ```
2. **Via GUI Windows:**
   * Buka *Start Menu* -> ketik *Environment Variables* -> pilih *Edit the system environment variables*.
   * Klik tombol *Environment Variables...* -> pada bagian *System variables*, klik *New...* dan tambahkan ketiga key-value di atas.
   * Restart aplikasi Ollama / service agar variabel terbaca permanen.

---

## 2. Anggaran Context Eksplisit (Context Budgeting)

Context target utama adalah **8192 token** (telah terbukti stabil di VRAM 2.6 GB):

| Komponen Context | Alokasi Token | Keterangan & Kebijakan Truncation |
| :--- | :--- | :--- |
| **System Prompt Base** | ~250 token | Statis, di awal prompt (prefix cache). |
| **Profile (`profile.json`)** | Max 500 token | Statis, di awal prompt (prefix cache). Ditolak jika over 500 token. |
| **Schema 6 Tools Native** | ~800–1200 token | Definisi JSON schema tools untuk Ollama function calling. |
| **Riwayat Chat (History)** | ~1500–2000 token | Sliding window turn aktif. |
| **Tool Output (Per Aksi)** | **Max 1000 token** | Dipotong tegas: `"... [Output dipotong: sisa N baris]"`. |
| **Total Observasi Per Turn** | **Max 2000 token** | Akumulasi observasi multi-step ReAct dibatasi 2000 token. Observasi langkah lama dipangkas otomatis jika melampaui batas. |
| **Slack / Output Generation** | ~2000–3000 token | Ruang aman untuk ReAct loop & generasi respon. |
| **Total `num_ctx`** | **8192 token** | Diset eksplisit di setiap request payload ke Ollama. |

*Perangkuman riwayat chat dijalankan saat **IDLE**, bukan di tengah giliran percakapan.*

---

## 3. Sistem Belajar & Memori Deterministik (Anti-Gagal)

Berdasarkan temuan Tahap 0 (model 3B gagal memanggil tool memori saat user bertanya implisit seperti *"kamu ingat solusinya apa?"*), sistem memori diperkuat dengan pendekatan deterministik berlapis:

### A. Recall Otomatis Deterministik (Intersepsi Kode)
* **Pemicu Kata Kunci Leksikal:**
  Jika pesan user mengandung salah satu pemicu:  
  `["kemarin", "pernah", "ingat", "sebelumnya", "tadi", "waktu itu", "remember", "earlier"]`
* **Mekanisme:**
  Kode Python **langsung menjalankan pencarian SQLite FTS5** di `sessions.db` dan menyuntikkan top 2–3 cuplikan percakapan masa lalu ke dalam prompt turn tersebut **secara otomatis tanpa menunggu LLM memanggil tool**.
* **Tool `search_memory(query)` Tetap Tersedia:**
  Berfungsi sebagai cadangan jika user memberikan instruksi eksplisit pencarian topik yang tidak terjangkau kata pemicu otomatis.

### B. Reflector Daemon (Pembelajaran Terstruktur)
* **Hanya Ekstraksi Pesan User:** Respon ENKI diabaikan; fakta baru hanya bersumber dari ketikan user.
* **Validasi Substring Kode (`source_quote`):** Kode Python memeriksa apakah string kutipan benar-benar ada di pesan user. Jika tidak cocok 100% verbatim, usulan langsung **dibuang otomatis**.
* **Constrained Decoding Murni:** Menggunakan parameter `format` Pydantic (tanpa parameter `tools`, sesuai temuan kompatibilitas Tahap 0).
* **Key-Based Upsert & Deduplikasi:** Fakta yang cocok dengan key lama diperbarui, bukan ditumpuk.
* **Proposal Queue:** Tersimpan di `pending_proposals.json` sampai disetujui via `/memory approve <id>`.

### C. Pengujian Memori Deterministik
1. **Unit Test Perakitan Prompt (Tanpa LLM):** Memastikan fakta dari `profile.json` dan hasil recall FTS5 selalu hadir dalam teks prompt hasil rakitan.
2. **Integration Test Token Unik Lintas Sesi:** Inject fakta unik (`TOKEN_PROYEK_XYZ99`) -> simpan & approve -> buka sesi bersih -> assert kemunculan token di respons teks.

---

## 4. Definisi & Perbedaan 6 Tools Native (Menghilangkan Ambiguitas)

Pada pengujian Spike prompt ID 26 (*"periksa apa saja dependency di requirements.txt"*), model sempat bingung antara membaca file atau menjalankan perintah shell. Deskripsi 6 tools diperjelas secara tegas:

1. `search_memory(query: str)`:
   * *Deskripsi:* Cari catatan atau riwayat obrolan masa lalu di database memori (FTS5). Gunakan jika informasi yang dicari berada di percakapan lampau.
2. `screen_inspect(target: str)`:
   * *Deskripsi:* Tangkap dan baca teks yang sedang tampil di layar/window aktif menggunakan OCR.
3. `web_fetch(url: str)`:
   * *Deskripsi:* Ambil dan ekstrak konten teks utama dari URL web/artikel.
4. `open_url(url: str)`:
   * *Deskripsi:* Buka URL di browser peramban default pengguna.
5. `read_file(file_path: str)`:
   * *Deskripsi:* **Gunakan HANYA untuk membaca/melihat isi teks file dokumen atau kode program** (misalnya .py, .json, .txt, .md, requirements.txt). **JANGAN gunakan untuk mengeksekusi script atau menjalankan instalasi.**
6. `run_shell_command(command: str)`:
   * *Deskripsi:* **Gunakan HANYA untuk mengeksekusi perintah terminal/shell** (misalnya git, pytest, dir, python script.py, pip install). **JANGAN gunakan hanya untuk sekadar membaca isi file teks (gunakan read_file).**

---

## 5. Keamanan & Sandboxing (Pertahanan Prompt Injection)

1. **Sanitasi Closing Tag:**
   Kode Python membuang/escape semua string penutup `</untrusted_external_content>` dari teks OCR layar dan web sebelum disuntikkan ke prompt.
2. **Pengakuan Batas Model 3B:**
   Model 3B tidak bisa diandalkan kebal jailbreak/injection. Pertahanan utama adalah **konfirmasi manual `[Y/n]` untuk semua aksi tulis/modifikasi file dan eksekusi shell**.
3. **Path Traversal Guard:**
   Semua path file wajib melewati `os.path.resolve()` dan divalidasi berada dalam root workspace yang diizinkan.
4. **Shell Allowlist:**
   Perintah shell dibatasi pada daftar aman yang terdaftar.

---

## 6. Voice Output (TTS) & Zero-Cost Mute

*Voice Input (STT) telah resmi **DIBUANG PERMANEN**.*

1. **Zero-Cost Mute:**
   Status mute dicek sebelum pipeline audio dipanggil. Jika mute: **0 sintesis, 0 thread, 0 library audio**.
2. **Evaluasi Suara Nyata Windows di Tahap 4:**
   * Keputusan akhir suara **akan dimulai dari enumerasi daftar suara Indonesia yang benar-benar terdeteksi di Windows pengguna** (via Windows OneCore / SAPI5), tidak mengasumsikan nama suara.
   * `edge-tts` disiapkan sebagai opsi online sekunder jika diinginkan suara neural cloud.
3. **Pembatasan Beban CPU:**
   Proses sintesis dibatasi ke **1–2 core CPU** (menggunakan setelan thread affinity / `OMP_NUM_THREADS=2`).
4. **Interupsi Instan:**
   Input baru dari user langsung memicu cancel flag dan mengosongkan antrean audio.

---

## 7. Roadmap Pelaksanaan

### Tahap 1: Core CLI Shell & Context Budgeting
* Setup folder di `D:\ENKI\ENKI`.
* Implementasi `config.json` (termasuk status mute) dan console CLI transparan (hanya `[Tool call]`, `[Tool result]`, `[Jawaban]`).
* Implementasi prompt assembler dengan prefix caching, pemotongan hard limit tool output (1000 token), dan batas total observasi (2000 token).
* Ukur ulang baseline VRAM saat VS Code dan browser sedang terbuka aktif.

### Tahap 2: Memory Engine (FTS5 + Recall Deterministik + Reflector Strict)
* SQLite logging di `sessions.db`.
* **Implementasi Recall Otomatis Deterministik:** Intersepsi keyword (*kemarin, pernah, ingat*, dll) langsung menarik top 2–3 cuplikan FTS5 ke prompt.
* Implementasi fallback tool `search_memory(query)`.
* Reflector daemon (hanya pesan user, verifikasi verbatim `source_quote`, key-based upsert, skema `format` Pydantic).
* Proposal queue (`/memory pending`, `/memory approve <id>`).
* Unit test assembler (tanpa LLM) & integration test token unik lintas sesi.

### Tahap 3: ReAct Loop, Multi-Turn Benchmark & Model Switcher
* Loop ReAct terkontrol (maks 5 iterasi, temperature 0.2, error feedback jika argumen salah).
* **Multi-Turn Benchmark Set Kedua:**
  * Menyiapkan 15–20 prompt BARU (termasuk 10–15 prompt dengan gaya bahasa santai/berantakan seperti orang ngetik sungguhan dengan singkatan & typo).
  * Pengujian alur multi-turn: `User input -> Tool call -> Tool execution -> Observasi kembali ke LLM -> Respon akhir`.
* Integrasi perintah switch model `/coder` (unload 3B chat, load 3B coder) dan catat waktu swap.
* Sandbox keamanan: `path.resolve()` workspace lock, shell allowlist, konfirmasi manual `[Y/n]`.

### Tahap 4: Voice Output (TTS) CPU dengan Zero-Cost Mute
* Implementasi zero-cost mute check.
* Enumerasi daftar suara Indonesia riil dari Windows pengguna (OneCore/SAPI5).
* Perdengarkan dan bandingkan sampel suara.
* Sentence chunking, thread affinity 1–2 core, dan interupsi instan.

### Tahap 5: Screen Reader (OCR Bounding Box)
* Deteksi active window bounding box.
* Komparasi langsung Windows Media OCR vs RapidOCR pada layar VS Code.
* Integrasi tool `screen_inspect`.

### Tahap 6: Web Tools (`open_url`, `web_fetch`) & Evaluasi Menyeluruh
* Implementasi `web_fetch` (trafilatura + requests) dengan sanitasi tag injection.
* Pengujian end-to-end seluruh kapabilitas ENKI.
