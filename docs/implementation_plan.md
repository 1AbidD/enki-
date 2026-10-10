# Rencana Implementasi: ENKI (Local AI Assistant) - v2.4

ENKI adalah asisten AI pribadi lokal yang dirancang khusus untuk hardware **NVIDIA RTX 3050 4GB VRAM**.
Arsitektur dan desain sistem ENKI dibangun secara sadar **di sekeliling keterbatasan nyata model 3B** (`qwen2.5:3b` dan `qwen2.5-coder:3b`), bukan menyangkalnya. Keterbatasan penalaran, rentannya model kecil terhadap prompt injection, serta kecenderungan halusinasi alat dimitigasi secara deterministik di level kode Python: antarmuka alat berargumen tetap (*fixed-argument tools*), deterministic memory gating, konfirmasi pengguna eksplisit `[y/N]`, dan penolakan kebijakan ReAct loop yang memaksa model kembali ke moda percakapan teks murni.

---

## 1. Status Model, Runtime Ollama & Kebijakan Hardware

### Hasil Pengukuran Riil & Status GPU
* **Versi Ollama:** `v0.33.0`
* **Kebijakan Runtime:** `OLLAMA_MAX_LOADED_MODELS=1` (kebijakan resmi produksi & benchmark).
  * Terbukti hanya **1 model tunggal** yang dimuat di VRAM pada satu waktu:
    * `qwen2.5:3b` : **100% GPU** (Ukuran: 2.3 GB, Context: 8192, VRAM Used: **2534 MiB**).
    * `qwen2.5-coder:3b` : **100% GPU** (Ukuran: 2.2 GB, Context: 4096, VRAM Used: **2386 MiB**). Mode coder berstatus **Chat-Only** (tool support tidak stabil pada 3B coder di Ollama).
    * `nomic-embed-text:latest` : **100% GPU** (Ukuran: 323 MB, VRAM Used: **636 MiB**).
  * Saat beralih model via `/coder` atau saat embedding dimuat di bawah kebijakan `=1`, model sebelumnya langsung dieviktasi otomatis dari VRAM.
  * *Catatan Pengukuran `=3`:* Eksperimen `=3` terbukti memungkinkan koeksistensi model di VRAM (chat, coder, embedding), namun statusnya adalah **terukur, tidak dipakai; embedding butuh >=2**. Kebijakan produksi dan benchmark tetap mengunci strictly `=1`.
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
* **Perangkuman History Saat Idle:** [BELUM DIIMPLEMENTASI] (Belum ada kode implementasi peringkasan saat status idle; saat ini riwayat dipangkas FIFO berbasis batas token konteks).

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

### B. Recall Pasif Deterministik (EKSPERIMENTAL, GAGAL GATE, DEFAULT OFF)
Mekanisme heuristik recall pasif berbasis aturan R3_strict (penanda konteks + Document Frequency $\text{DF} \le 5.0\%$) telah dievaluasi pada Corpus B realistis (294 turns):
* **Hasil Evaluasi Frozen Suite:** Menghasilkan False Positive Rate sebesar 61.90% (13/21 false proposals pada pertanyaan developer yang memuat kata 'kita'/'proyekku'), melampaui batas toleransi 10%.
* **Keputusan Desain:** **GAGAL GATE & NONAKTIF SECARA DEFAULT** (`"passive_recall": false` di `config.json`).
* **Strategi Memori Produksi:** Mengingat fakta sesi lampau mengandalkan profil permanen (`profile.json` via `ProfileManager`) serta pencarian aktif (Active Recall via pemicu leksikal kata waktu atau perintah pencarian eksplisit). Batasan ini dicantumkan secara transparan di `/help`.

#### Hasil Evaluasi Frozen Suite (21 Negatif Berpenanda & 13 Positif Tanpa Penanda):
* **Kelompok Negatif Berpenanda (Berbagi Topik Developer dengan Korpus):**
  * Korpus A (60 turns): False Proposals = 13/21 (61.90%), Wilson 95% CI = [40.88%, 79.25%].
  * Korpus B (294 turns): False Proposals = 13/21 (61.90%), Wilson 95% CI = [40.88%, 79.25%].
  * *Temuan Empiris & Gate:* False Proposal rate pada kelompok negatif berpenanda melebihi ambang batas 10% (karena pertanyaan teknis umum yang memakai kata ganti "kita" mencocokkan turn historis di mana topik teknis tersebut hanya muncul 1–2 kali sehingga DF-nya $\le 5\%$). Sesuai protokol, proses **GAGAL GATE DAN DIMATIKAN DEFAULT**.
* **Kelompok Positif Tanpa Penanda (Keterbatasan Model):**
  * Korpus A & B: Recall = 0/13 (0.00%), Wilson 95% CI = [0.00%, 22.81%].
  * *Keterbatasan Terdokumentasi:* Aturan berbasis penanda leksikal tidak dapat memicu recall pasif pada pertanyaan memori yang tidak menyertakan penanda konteks berbagi.

#### Hasil Pengukuran Riil R4 (Embedding nomic-embed-text di CPU/GPU):
* **Model:** `nomic-embed-text:latest` (vektor 768 dimensi).
* **Latensi CPU (`num_gpu=0`):** Dingin 858.69 ms (diukur), Panas 40.03 ms (diukur).
* **Latensi GPU (Ollama default):** Dingin 26,156.34 ms (diukur), Panas 43.51 ms (diukur).
* **Konsumsi Memori Host:** RAM sistem sebelum 12,078.7 MB, sesudah 12,367.4 MB (delta: +288.8 MB) (diukur).
* **Verifikasi Eviction VRAM (`ollama ps`):**
  * Di bawah konfigurasi CPU (`num_gpu=0`), RAM host bertambah tanpa menyentuh VRAM GPU.
  * Di bawah konfigurasi default GPU dengan `OLLAMA_MAX_LOADED_MODELS=1`, memuat embedding ke VRAM secara deterministik mendepak (evict) model LLM chat/coder. Koeksistensi dual-model di VRAM terukur membutuhkan `OLLAMA_MAX_LOADED_MODELS >= 2` (diukur pada `=3`, namun tidak diadopsi demi kebijakan produksi `=1`).

#### Known Issues (Keterbatasan Teknis Passive Recall):
1. **Token Absen Ber-DF 0%:** Token yang sama sekali tidak pernah muncul di corpus memiliki Document Frequency (DF) = 0.0%, sehingga secara matematis lolos ambang batas kelangkaan $\text{DF} \le 5.0\%$.
2. **Aturan $\ge 2$ Token:** Aturan pencocokan $\ge 2$ token tetap dapat terpicu jika terdapat 1 token generik + 1 token langka/absen di dalam turn yang sama.
3. **Pembersihan Hack Korpus Sintetis:** Hack `if total < 20` di `enki/memory/database.py` telah dihapus permanen. Penghitungan DF kini murni matematis $(\text{matches} / \text{total}) \times 100\%$, dan pengujian unit menggunakan korpus realistis ($\ge 25$ turn).

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
  * Usulan tersimpan di `pending_facts.json` (di bawah folder data/memory) hingga disetujui via `/memory approve <id>`.
  * Daftar perintah CLI lengkap (persis output `/memory help`):
    - `/memory view`              : Lihat seluruh fakta profil aktif
    - `/memory pending`           : Lihat antrean usulan fakta baru
    - `/memory approve <id>`      : Setujui usulan fakta dan simpan ke profil
    - `/memory reject <id>`       : Tolak usulan fakta
    - `/memory edit <id> <val>`   : Koreksi nilai usulan fakta sebelum disetujui
    - `/memory delete <key>`      : Hapus fakta dari profil
    - `/memory log`               : Lihat riwayat audit perubahan profil
    - `/memory stats`             : Lihat metrik usulan (diterima, ditolak, diedit, dibuang filter)
    - `/reflect`                  : Jalankan refleksi manual atas sesi yang belum direfleksi

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
* **Set 3 (Tahap 3 Eks-Ujian, 24 Prompt x 3 Runs):** Status **DEV SET 3 (Eks-Ujian)**. Gagal gate.
  * *Hasil Hitung Ulang dengan Aturan Timeout = Gagal:* S3_P24_NEG run 3 (timeout) dikoreksi menjadi FAIL.
    - Success Rate: **47/72 (65.3%)** (angka lama: 48/72, 66.7%)
    - Routing OK: **49/72 (68.1%)** (angka lama: 50/72, 69.4%)
    - Fidelity OK: **47/72 (65.3%)** (angka lama: 48/72, 66.7%)
  * *Catatan Waktu Proses:* Sekuensialitas eksekusi Set 2 dan Set 3 berstatus **tidak terbukti** karena timestamp interaksi antarmuka tidak mencerminkan lifecycle proses OS.
* **Set 4R (Regresi Tahap 3, 10 Prompt x 3 Runs):** Status **REGRESSION SET (TIDAK IKUT GATE)**. Menguji 10 prompt bermasalah dari Set 3 (false call shell lama, timeout) untuk membuktikan tidak adanya regresi.
* **Set 4A (Ujian Akhir, 30 Prompt x 3 Runs):** Status **UJIAN AKHIR (MENENTUKAN GATE)**.

### Perbaikan Harness & Evaluator Benchmark (Aturan Mutlak):
1. **Timeout = Gagal:** Di seluruh evaluator benchmark (Set 2, Set 3, Set 4), jika `is_timeout == True`, maka `is_success`, `routing_ok`, dan `fidelity_ok` wajib `False`.
2. **Pencatatan Waktu ISO UTC:** Setiap turn mencatat `started_at` dan `ended_at` dalam format ISO UTC (`YYYY-MM-DDTHH:MM:SS.ffffffZ`).
3. **BenchmarkLock Anti-Konkurensi:** Menyimpan PID dan timestamp mulai. Jika lock dipegang PID yang masih hidup (`psutil.pid_exists(pid) == True`), runner menolak keras. Jika dipegang PID yang sudah mati (stale lock), lock diambil alih otomatis dengan pesan log jelas.
4. **Klasifikasi Prompt MEM Set 4A:** Runner mengklasifikasikan prompt MEM menjadi 3 kelompok: `has_active_trigger`, `has_marker`, dan `none`. Gate evaluasi HANYA dihitung dari kelompok `active + marker`. Kelompok `none` dilaporkan terpisah sebagai keterbatasan teridentifikasi.

### Kriteria Kelulusan Set 4A (Ditulis Di Muka Sebelum Run):
1. **Routing Positif:** $\ge 90.0\%$ (dihitung per prompt unik $n$ dan per run)
2. **False Call Negatif:** $\le 10.0\%$ (termasuk yang diblokir sandbox, prompt konfirmasi dilaporkan terpisah)
3. **Fidelity Jawaban:** $\ge 85.0\%$ dari run yang memanggil tool yang benar
4. **Total Timeout:** $= 0$
5. **Argumen Invalid:** $= 0$
6. **0 Recall Block pada Negatif Tanpa Pemicu:** $0$ kasus injeksi recall tidak sah pada prompt negatif murni tanpa pemicu.
7. **Kriteria Sukses MEM:** Nilai kunci wajib muncul di jawaban akhir DAN tidak memanggil tool lain.
8. **Validasi Komposisi MEM:** Dari 12 prompt MEM Set 4A (6 built-in mode profile + 6 user), wajib minimal 4 prompt berstatus Mode Recall. Runner melakukan hard fail jika mode recall $< 4$.

### Komposisi 30 Prompt Set 4A:
* **12 Memori (MEM):** 6 buatan asisten (Mode Profile via `profile.json`), 6 dari user (diharapkan 4 Mode Recall + 2 Mode Profile).
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
* Aturan resmi recall pasif R3_strict dan pengukuran latensi/RAM embedding CPU R4.
* Suite unit test keamanan, isolasi, shell tools, policy rejection, passive recall, prompt injection, harness evaluator, benchmark mock runners, dan benchmark lock (78 unit test terdaftar secara resmi di docs/test_ids.txt: 75 lulus, 3 benchmark dilewati saat discovery via skipTest).
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
