# Rencana Implementasi: ENKI (Local AI Assistant) - v2.4

ENKI adalah asisten AI pribadi lokal yang dirancang khusus untuk hardware **NVIDIA RTX 3050 4GB VRAM**.
Arsitektur dan desain sistem ENKI dibangun secara sadar **di sekeliling keterbatasan nyata model 3B** (`qwen2.5:3b` dan `qwen2.5-coder:3b`), bukan menyangkalnya. Keterbatasan penalaran, rentannya model kecil terhadap prompt injection, serta kecenderungan halusinasi alat dimitigasi secara deterministik di level kode Python: antarmuka alat berargumen tetap (*fixed-argument tools*), deterministic memory gating, konfirmasi pengguna eksplisit `[y/N]`, dan penolakan kebijakan ReAct loop yang memaksa model kembali ke moda percakapan teks murni.

---

## 1. Status Model, Runtime Ollama & Kebijakan Hardware

### Hasil Pengukuran Riil & Status GPU
* **Versi Ollama:** `v0.33.0`
* **Model Terpasang & Verifikasi `ollama ps`:**
  * Terbukti hanya **1 model tunggal** yang dimuat di VRAM pada satu waktu:
    * `qwen2.5:3b` : **100% GPU** (Ukuran: 2.3 GB, Context: 8192, VRAM Used: **2774 MiB**, Free: **1189 MiB**).
    * `qwen2.5-coder:3b` : **100% GPU** (Ukuran: 2.3 GB, Context: 8192, VRAM Used: **2773 MiB**, Free: **1190 MiB**). Mode coder berstatus **Chat-Only** (tool support tidak stabil pada 3B coder di Ollama).
  * Saat beralih model via `/coder`, model chat 3B langsung dieviktasi otomatis dari VRAM berkat kebijakan `OLLAMA_MAX_LOADED_MODELS=1`.
* **Reflector Model Safety:** `reflect_session` dan perintah `/reflect` mengunci pemanggilan model ke `qwen2.5:3b`, menjamin refleksi memori tidak pernah berjalan pada model coder.
* **Pengakuan Batas Model 3B:** Model 3B tidak dapat diandalkan untuk menyaring serangan jailbreak atau memvalidasi string shell bebas secara otonom. Oleh karena itu, string perintah bebas dihapus total, dan seluruh aksi sistem wajib dikonfirmasi manusia `[y/N]`.

### Cara Set Environment Variable Ollama Secara Permanen di Windows
Konfigurasi diset pada level scope **'User'**:
```powershell
[System.Environment]::SetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS', '1', 'User')
[System.Environment]::SetEnvironmentVariable('OLLAMA_NUM_PARALLEL', '1', 'User')
[System.Environment]::SetEnvironmentVariable('OLLAMA_FLASH_ATTENTION', '1', 'User')
```

---

## 2. Anggaran Context Dinamis (Dynamic Context Budgeting)

Target context runtime adalah **8192 token** (`num_ctx = 8192`). Anggaran dikelola secara **dinamis berbasis pengukuran riil**:

$$\text{Sisa Token} = \text{num\_ctx} - (\text{System Base} + \text{Profile} + \text{Schema Tools} + \text{History Window})$$

* **Perhitungan Token Riil:** Dihitung langsung menggunakan metrik `prompt_eval_count` dari respon Ollama API, diverifikasi dengan estimasi lokal (`estimate_tokens_local`) sebagai batas atas sebelum pengiriman request.
* **Overhead Statis (~1500–2000 token):**
  * System Prompt Base: ~250 token (hanya aturan dan perilaku, tanpa fakta diri user).
  * Profile (`profile.json`): Max 500 token (fakta tentang user dimuat eksklusif dari sini). Ditolak jika over 500 token.
  * Schema Tools Tetap: ~600–900 token.
* **Pembagian Sisa Token (Observasi Tool vs Respon Model):**
  * Observasi tool per aksi: Maksimal 1000 token (dipotong tegas dengan penanda `... [Output dipotong: sisa N baris]`).
  * Pemangkasan observasi ReAct: Observasi langkah terdahulu dipadatkan otomatis menjadi ringkasan satu baris jika ReAct loop berlanjut.
  * Batas generasi respon per request: `num_predict = 1024`, dengan per-request HTTP timeout = 60 detik.
* **Perangkuman History:** Dijalankan saat sistem berstatus **IDLE**, bukan di tengah giliran percakapan aktif.

---

## 3. Sistem Memori: Dual Recall (Aktif & Pasif) + Reflector Strict

### A. Recall Aktif Deterministik (Pemicu Leksikal)
* **Pemicu Regex Word-Boundary:**
  `\b(kemarin|pernah|ingat|sebelumnya|tadi|waktu itu|remember|earlier)\b`
* **Query Builder:** Kata pemicu dan stopword percakapan dibuang; query FTS5 menggunakan kata substantif unik dengan operator `OR`.
* **Grounding Keras:**
  ```text
  [Instruksi Wajib]: Jawab berdasarkan hasil pencarian di atas. Jika hasil memuat jawabannya, sampaikan isinya. Jangan menambah saran umum.
  ```

### B. Recall Pasif Deterministik (Terkalibrasi DF & Dibekukan)
Untuk pertanyaan tanpa pemicu waktu (misal: *"apa database utama proyek kita ya?"*), FTS5 dijalankan secara pasif dengan **ambang batas Document Frequency (DF)**:
1. **Definisi Token Spesifik vs Umum:**
   * Token dianggap **Umum** jika Document Frequency di corpus turn lampau $\text{DF} > 5.0\%$. Kata umum tidak boleh memicu recall sendirian.
   * Token dianggap **Spesifik / Domain** jika $\text{DF} \le 5.0\%$.
2. **Kondisi Lolos Injeksi (DIBEKUKAN pada DF $\le 5.0\%$):**
   * Turn kandidat mencocokkan **minimal 2 kata substantif berbeda** dalam turn yang sama, ATAU
   * Mencocokkan **minimal 1 kata spesifik** yang memiliki $\text{DF} \le 5.0\%$.
3. **Format Blok Konteks Latar (Tanpa Grounding Keras):**
   ```text
   <untrusted_memory_content source="sqlite_fts5_passive_recall">
   [Konteks Latar dari Percakapan Lama] (Gunakan HANYA jika relevan langsung dengan pertanyaan pengguna, abaikan jika tidak relevan):
   - User: ...
   - Assistant: ...
   </untrusted_memory_content>
   ```
4. **Kapasitas Injeksi:** Maksimal 3 cuplikan teratas, maksimal 300 token.

### C. Reflector Daemon & Proposal Queue
* **Eksklusif Pesan User:** Hanya pesan pengguna yang diekstraksi; respon asisten diabaikan sepenuhnya.
* **Verifikasi Substring Verbatim (`source_quote`):** Kode Python memeriksa apakah string kutipan benar-benar ada secara verbatim di pesan user. Quote recovery hanya menghasilkan potongan teks asli dari pesan user.
* **Filter Hipotesis Deterministik:** Usulan yang mengandung kata pengandaian (*seandainya, andaikan, kalau aku jadi, if I were*) ditolak otomatis di kode, sedangkan preferensi legal (*kalau kasih kode, tolong sertakan...*) tetap lolos.
* **Filter Rahasia Deterministik:** Pola credential (API key, token, private key, password) dibuang otomatis di level kode.
* **Proposal Queue & CLI `/memory`:**
  * Usulan tersimpan di `pending_proposals.json` hingga disetujui via `/memory approve <id>`.
  * Perintah tersedia: `/memory pending`, `/memory approve <id>`, `/memory reject <id>`, `/memory list`, `/memory stats`.

---

## 4. Definisi Tool Berargumen Tetap (Fixed Argument Tools)

String perintah shell bebas (`run_shell_command`) telah **DIHAPUS PERMANEN**. Seluruh interaksi menggunakan alat berargumen tetap tanpa celah parameter injection:

1. `search_memory(query: str)`:
   * *Gating Deterministik:* Hanya dimuat dalam schema tools jika pesan mengandung pemicu recall leksikal atau kata kunci pencarian eksplisit (*catatan, riwayat, log obrolan, sesi lampau, memori*).
2. `read_file(file_path: str)`:
   * Membaca isi file teks di dalam workspace. Path diisolasi via `Path.resolve()`, direktori sensitif (`.git`, `data/`, `logs/`, credentials) diblokir keras.
3. `git(action: enum["status", "log", "diff"])`:
   * Menjalankan perintah Git terdaftar. Memerlukan konfirmasi manual pengguna `[y/N]` yang menampilkan perintah persis sebelum dieksekusi.
4. `python_version()`:
   * Menjalankan `python --version`. Memerlukan konfirmasi pengguna `[y/N]`.
5. `run_unittests()`:
   * Menjalankan `python -m unittest`. Memerlukan konfirmasi pengguna `[y/N]`.
6. `list_dir(path: Optional[str])`:
   * Membaca daftar file dan folder di dalam workspace secara aman via Python `Path.iterdir()` (tanpa subprocess shell). Path traversal di luar workspace diblokir keras.
7. **Audit Log:** Seluruh eksekusi tool dicatat ke `tool_audit.log` (mendukung path injection untuk pengujian).

---

## 5. Keamanan & Kebijakan ReAct Loop

1. **Sanitasi Tag Untrusted Tunggal:**
   Semua konten eksternal (output tool, file, recall FTS5) dibersihkan menggunakan regex tunggal `sanitize_untrusted_text` untuk melucuti tag pembuka dan penutup `<untrusted_*>` sebelum dibungkus ke dalam prompt.
2. **Path Traversal Guard:**
   Semua path file wajib melewati `Path.resolve()` dan divalidasi `is_relative_to(workspace_root)`. UNC path (`\\` atau `//`) diblokir seketika.
3. **Penolakan Kebijakan (Policy Rejection):**
   Jika terjadi pelanggaran kebijakan (path traversal ke luar workspace, penolakan konfirmasi `[y/N]` oleh pengguna, pemanggilan tool yang tidak terdaftar, atau loop guard identik berturut-turut), observasi error dicatat dan iterasi ReAct berikutnya dikirim **TANPA TOOLS (`tools=None`)**. Model dipaksa merespons dengan teks penjelasan tanpa opsi memanggil tool lagi.
4. **Retry Argumen:**
   Kesalahan validasi argumen hanya diperbolehkan retry maksimal 1 kali. Jika gagal lagi, dianggap sebagai pelanggaran kebijakan (`tools=None`).

---

## 6. Voice Output (TTS) & Zero-Cost Mute

*Voice Input (STT) dan Avatar 3D telah resmi **DIBUANG PERMANEN** untuk menghemat resource dan fokus pada keandalan sistem lokal.*

1. **Zero-Cost Mute:**
   Status mute dicek sebelum pipeline audio dipanggil. Jika mute aktif: **0 sintesis, 0 thread background, 0 library audio di-load**.
2. **Evaluasi Suara Nyata Windows (Tahap 4):**
   * Keputusan akhir suara akan dimulai dari enumerasi daftar suara Indonesia yang benar-benar terdeteksi di Windows pengguna (via Windows OneCore / SAPI5).
   * Opsi fallback: `edge-tts` (online sekunder).
3. **Pembatasan Beban CPU:**
   Proses sintesis dibatasi ke **1–2 core CPU** (thread affinity / `OMP_NUM_THREADS=2`).
4. **Interupsi Instan:**
   Input baru dari user di konsol langsung memicu cancel flag dan mengosongkan antrean pemutaran audio seketika.

---

## 7. Status Rangkaian Benchmark & Pengujian

* **Set 1 (Tahap 0 Spike, 30 Prompt):** Status **DEV SET 1**. Akurasi tool-calling 91.11%.
* **Set 2 (Tahap 3 Dev Set, 20 Prompt x 3 Runs):** Status **DEV SET 2**. Akurasi 81.7%. Menunjukkan keandalan grounding WAL (3/3 pass) dan eliminasi false call `search_memory` via deterministic gating.
* **Set 3 (Tahap 3 Eks-Ujian, 24 Prompt x 3 Runs):** Status **DEV SET 3 (Eks-Ujian)**. Gagal gate (routing 61.1%, false call shell 22.2%). Menjadi dasar arsitektur fixed argument tools dan kalibrasi recall pasif.
* **Set 4R (Regresi Tahap 3, 10 Prompt x 3 Runs):** Status **REGRESSION SET (TIDAK IKUT GATE)**. Menguji 10 prompt bermasalah dari Set 3 (false call shell lama, timeout) untuk membuktikan tidak adanya regresi.
* **Set 4A (Ujian Akhir, 30 Prompt x 3 Runs):** Status **UJIAN AKHIR (MENENTUKAN GATE)**.

### Kriteria Kelulusan Set 4A (Ditulis Di Muka Sebelum Run):
1. **Routing Positif:** $\ge 90.0\%$ (dihitung per prompt unik $n$ dan per run)
2. **False Call Negatif:** $\le 10.0\%$ (termasuk yang diblokir sandbox, prompt konfirmasi dilaporkan terpisah)
3. **Fidelity Jawaban:** $\ge 85.0\%$ dari run yang memanggil tool yang benar
4. **Total Timeout:** $= 0$
5. **Argumen Invalid:** $= 0$
6. **Injeksi Recall Pasif ke Prompt Negatif:** $\le 10.0\%$

### Komposisi 30 Prompt Set 4A:
* **12 Positif Memori (Recall Pasif):** 6 dari user, 6 buatan asisten.
* **6 Positif Workspace / Git / Test:** 4 dari user, 2 buatan asisten.
* **12 Negatif Murni (No Tool):** 5 dari user, 7 buatan asisten.

---

## 8. Roadmap Pelaksanaan

### Tahap 1: Core CLI Shell & Context Budgeting (SELESAI - LULUS)
* Struktur folder di `D:\ENKI\ENKI`.
* `config.json` (status mute) dan console trace `[Tool call]`, `[Tool result]`, `[Jawaban]`.
* Prompt assembler dengan dynamic context budgeting dan token truncation.
* Suite unit test assembler tanpa LLM (4 unit test lulus).

### Tahap 2: Memory Engine & Dual Recall FTS5 (SELESAI - LULUS)
* Skema SQLite `sessions.db` (tabel sessions, turns, virtual table turns_fts).
* Recall aktif dengan keyword boundaries dan stopword cleaning.
* Reflector daemon (eksklusif user turns, verbatim quote recovery, filter hipotesis & filter rahasia).
* Proposal queue dan perintah CLI `/memory`.
* Suite unit test memori terisolasi (14 unit test lulus).

### Tahap 3: ReAct Loop, Fixed Argument Tools & Sandbox (DALAM PROSES)
* Desain ulang tools ke format tetap: `git`, `python_version`, `run_unittests`, `list_dir`, `read_file`, `search_memory`.
* Kebijakan ReAct loop: Penolakan kebijakan langsung memaksa `tools=None` pada langkah berikutnya.
* Kalibrasi Document Frequency (DF $\le 5.0\%$) untuk recall pasif pada corpus realistis (>250 turns).
* Suite unit test keamanan, isolasi, shell tools, policy rejection, passive recall, prompt injection (50 unit test lulus).
* Persiapan pengujian Set 4A dan Set 4R.

### Tahap 4: Voice Output (TTS) CPU dengan Zero-Cost Mute (AKAN DATANG)
* Implementasi zero-cost mute check sebelum audio pipeline.
* Enumerasi daftar suara Indonesia nyata dari sistem Windows pengguna (OneCore / SAPI5).
* Komparasi sampel suara langsung di perangkat pengguna.
* Sentence chunking, thread affinity 1–2 core CPU, dan interupsi instan.

### Tahap 5: Screen Reader (OCR Bounding Box) (AKAN DATANG)
* Deteksi bounding box window aktif.
* Komparasi langsung Windows Media OCR vs RapidOCR pada layar VS Code.
* Integrasi tool `screen_inspect`.

### Tahap 6: Web Tools (`open_url`, `web_fetch`) & Evaluasi Menyeluruh (AKAN DATANG)
* Implementasi `web_fetch` (trafilatura + requests) dengan sanitasi tag injection.
* Integrasi `open_url` aman.
* Pengujian integrasi ujung ke ujung seluruh kapabilitas ENKI.
