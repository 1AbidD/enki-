# Rencana Implementasi: ENKI (Local AI Assistant) - v2.9

*Ringkasan Perubahan v2.9: hasil kelulusan Tahap 4B-1 (preservasi identifier/path, koreksi batas CPU SAPI5, default mute, dan telemetri child process).*

ENKI adalah asisten AI pribadi lokal yang dirancang khusus untuk hardware **NVIDIA RTX 3050 4GB VRAM**.
Arsitektur dan desain sistem ENKI dibangun secara sadar **di sekeliling keterbatasan nyata model 3B** (`qwen2.5:3b` dan `qwen2.5-coder:3b`), bukan menyangkalnya. Keterbatasan penalaran, rentannya model kecil terhadap prompt injection, serta kecenderungan halusinasi alat dimitigasi secara deterministik di level kode Python: antarmuka alat berargumen tetap (*fixed-argument tools*), deterministic memory gating, konfirmasi pengguna eksplisit `[y/N]`, dan penolakan kebijakan ReAct loop yang memaksa model kembali ke moda percakapan teks murni.

---

## 1. Status Model, Runtime Ollama & Kebijakan Hardware

### Hasil Pengukuran Riil & Status GPU
* **Versi Ollama:** `v0.33.0`
* **Kebijakan Runtime:** `OLLAMA_MAX_LOADED_MODELS=1` (kebijakan resmi produksi & benchmark).
  * Terbukti hanya **1 model tunggal** yang dimuat di VRAM pada satu waktu (diukur di `docs/evidence/ollama_measure.txt`):
    * `qwen2.5:3b` : **100% GPU** (Ukuran: 2.3 GB, Context: 8192, VRAM Used: **2641 MiB**; GPU Free: 1322 MiB).
    * `qwen2.5-coder:3b` : **100% GPU** (Ukuran: 2.2 GB, Context: 4096, VRAM Used: **2493 MiB**; GPU Free: 1470 MiB). Mode coder berstatus **Chat-Only** (tool support tidak stabil pada 3B coder di Ollama).
    * `nomic-embed-text:latest` : **100% GPU** (Ukuran: 323 MB, Context: 2048, VRAM Used: **743 MiB**; GPU Free: 3220 MiB).
  * *Catatan Pengukuran `=3`:* Eksperimen `=3` membuktikan koeksistensi model coder (2.2 GB) + nomic-embed-text (323 MB) pada 2779 MiB VRAM. Model chat (`qwen2.5:3b`) dan coder (`qwen2.5-coder:3b`) tidak pernah berdampingan bersamaan di VRAM. Status pengukuran ini adalah **terukur, tidak dipakai; embedding butuh >=2**. Kebijakan resmi produksi dan benchmark mengunci strictly `=1`.
  * *Kondisi Eksekusi Set 2 & Set 3:* Klaim bahwa Set 2 dan Set 3 dijalankan di bawah environment default berstatus **kemungkinan, belum terbukti** (log tugas 11:17 dan 15:03 mencatat env default, namun tidak ada log server config di rentang 12:43–12:52 WIB saat benchmark berjalan, dan berkas JSONL lama tidak mencatat header environment).
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
   * *Gating Deterministik:* Hanya dimuat dalam schema tools jika pesan mengandung pemicu recall aktif leksikal (`kemarin|pernah|ingat|sebelumnya|tadi|waktu itu|remember|earlier`), atau pemicu percakapan: kata `riwayat` HANYA bersama `percakapan|obrolan|chat|diskusi` (`\briwayat\b.*\b(percakapan|obrolan|chat|diskusi)\b|\b(percakapan|obrolan|chat|diskusi)\b.*\briwayat\b`), kata `catatan` HANYA bersama `obrolan|percakapan|chat` atau `catatan lama` (`\bcatatan\s+lama\b|\bcatatan\b.*\b(obrolan|percakapan|chat)\b|\b(obrolan|percakapan|chat)\b.*\bcatatan\b`), atau frasa eksplisit memori lain (`log obrolan`, `sesi lampau`, `sesi sebelumnya`, `percakapan lama`, `memori`). Kata `riwayat` atau `catatan` yang berdiri sendiri tanpa konteks percakapan secara deterministik **TIDAK membuka** `search_memory`.
2. `read_file(file_path: str)`:
   * Membaca isi file teks di dalam workspace. Path diisolasi via `Path.resolve()`, direktori sensitif (`.git`, `data/`, `logs/`, credentials) diblokir keras.
3. `git(action: enum["status", "log", "diff"])`:
   * Menjalankan perintah Git terdaftar. Memerlukan konfirmasi manual pengguna `[y/N]` yang menampilkan perintah persis sebelum dieksekusi.
4. `python_version()`:
   * Menjalankan `python --version`. Memerlukan konfirmasi pengguna `[y/N]`.
5. `run_unittests()`:
   * Menjalankan `python -m unittest discover [tests|.]`. Memerlukan konfirmasi pengguna `[y/N]`.
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
* **Set 2 (Tahap 3 Dev Set, 20 Prompt x 3 Runs):** Status **DEV SET 2 (tanpa raw 60 run, tidak dipakai)**. Berkas JSONL `benchmark_set2_raw.jsonl` di git (commit `17cdf1a`) hanya memuat 18 record; klaim 60 run (81.7%) tidak memiliki log raw di git sehingga tidak dipakai sebagai acuan gate.
* **Set 3 (Tahap 3 Eks-Ujian, 24 Prompt x 3 Runs):** Status **DEV SET 3 (Eks-Ujian)**. Gagal gate.
  * *Hasil Hitung Ulang dengan Aturan Timeout = Gagal:* S3_P24_NEG run 3 (timeout) dikoreksi menjadi FAIL.
    - Success Rate: **47/72 (65.3%)** (angka lama: 48/72, 66.7%)
    - Routing OK: **49/72 (68.1%)** (angka lama: 50/72, 69.4%)
    - Fidelity OK: **47/72 (65.3%)** (angka lama: 48/72, 66.7%)
  * *Catatan Waktu Proses:* Sekuensialitas eksekusi Set 2 dan Set 3 berstatus **tidak terbukti** karena timestamp interaksi antarmuka tidak mencerminkan lifecycle proses OS.
* **Set 4R (Regresi Tahap 3, 11 Prompt x 3 Runs):** Status **SELESAI (TIDAK IKUT GATE)** (Waktu Run UTC: 2026-10-12T02:51:44Z). Menguji 11 prompt regresi dari Set 3. Hasil: **24/33 turn lulus (72.7%)**, 8/11 prompt unik lulus. Giliran gagal: 9 turn (S4R_P05_REG 3 run, S4R_P06_REG 3 run, S4R_P07_REG 3 run).
* **Set 4A (Ujian Akhir, 30 Prompt x 3 Runs = 90 Turns):** Status **SELESAI (GAGAL GATE)** (Waktu Run UTC: 2026-10-12T02:51:44Z). Tingkat keberhasilan total: **78/90 turn (86.7%)**, 26/30 prompt unik lulus. Giliran gagal: 12 turn (S4A_U08_FILE 3 run, S4A_P18_MEM 3 run, S4A_P21_MEM 3 run, S4A_P26_NEG 3 run).
* **Catatan Insiden Hash Casing (2026-10-12):** Percobaan eksekusi pertama Set 4 mengalami crash di runner sebelum memproses satu prompt pun karena kunci SHA256 di `docs/set4_lock.txt` ditulis dalam huruf kapital sedangkan `hashlib.sha256().hexdigest()` menghasilkan huruf kecil. Masalah diselesaikan di commit `set4 lock lowercase hash` (`6a3008f`) dengan mengubah nilai kunci di lock menjadi huruf kecil tanpa memodifikasi kode runner. Tidak ada prompt yang diproses pada percobaan pertama tersebut.
* Bukti resmi adalah berkas yang ter-commit di git; kutipan "output mentah" di percakapan chat tidak dipakai sebagai bukti (ditemukan perbedaan antara kutipan chat dan berkas: Head Git, stempel waktu, daftar kunci MEM).

### Perbaikan Harness & Evaluator Benchmark (Aturan Mutlak):
1. **Timeout = Gagal:** Di seluruh evaluator benchmark (Set 2, Set 3, Set 4), jika `is_timeout == True`, maka `is_success`, `routing_ok`, dan `fidelity_ok` wajib `False`.
2. **Pencatatan Waktu ISO UTC:** Setiap turn mencatat `started_at` dan `ended_at` dalam format ISO UTC (`YYYY-MM-DDTHH:MM:SS.ffffffZ`).
3. **BenchmarkLock Anti-Konkurensi:** Menyimpan PID dan timestamp mulai. Jika lock dipegang PID yang masih hidup (`psutil.pid_exists(pid) == True`), runner menolak keras. Jika dipegang PID yang sudah mati (stale lock), lock diambil alih otomatis dengan pesan log jelas.
4. **Klasifikasi Mode MEM Set 4A (Mode Profile vs Mode Recall):** Runner mengklasifikasikan prompt MEM menjadi dua mode:
   - **Mode Profile:** Fakta tentang pengguna diinjeksi ke dalam berkas `profile.json` melalui `ProfileManager` dan dimuat dalam system prompt base.
   - **Mode Recall:** Fakta sesi masa lampau diinjeksi sebagai riwayat percakapan terdahulu di `sessions.db` (pernyataan natural pengguna dan balasan konfirmasi asisten) dan dicari menggunakan SQLite FTS5 Active Recall saat terdapat pemicu leksikal.
   - *Komposisi Wajib:* 12 MEM (terdiri dari mode profil dan mode recall, dengan syarat mutlak minimal 4 prompt Mode Recall), 6 workspace / git / test, dan 12 Negatif (minimal 4 berpemicu aktif).
5. **Gating Tool Deterministik Ketat & Trade-Off (Tercatat di Plan dan /help):**
   - Skema alat hanya diserahkan ke model jika pesan pengguna cocok dengan aturan leksikal spesifik (word-boundary, case-insensitive):
     - `read_file`: Ekstensi file yang dikenal (`py|md|txt|json|ya?ml|toml|cfg|ini|sh|csv|log`), ATAU prefix path `./` atau `../` atau huruf drive (misal `C:\` atau `D:/`), ATAU folder workspace (`app`, `tests`, `docs`, `enki`, `scripts`), ATAU kata kunci `file`/`berkas`. Istilah umum bergaris-miring (`dan/atau`, `input/output`, `TCP/IP`, `24/7`, `client/server`) secara deterministik **TIDAK membuka** `read_file`.
     - `git`: Kata `git`, `commit`, `branch`, atau `diff`. Kata `status`, `log`, `repo` HANYA memicu jika disertai kata `git`.
     - `run_unittests`: `unittest`, `unit test`, `pytest`, ATAU kata aksi (`jalankan|jalanin|run`) bersama kata tes (`test|tes`) DAN kata lingkup kode/proyek (`unit|proyek|project|kode|semua|seluruh`). Frasa tes non-software ("coba tes kamera", "cek tes suara", "test drive") secara deterministik **TIDAK membuka** `run_unittests`.
     - `python_version`: Kata `python` DAN (`versi` ATAU `version`).
     - `list_dir`: Kata `folder`, `direktori`, `ls`, `dir`.
     - `search_memory`: Pemicu recall aktif ATAU kata kunci pencarian memori eksplisit.
     - Jika tidak ada yang cocok: `tools=None` (mode obrolan teks murni tanpa alat).
   - *Trade-Off & Limitasi Gating Deterministik:*
     Pengukuran empiris false-open rate pada 20 kalimat obrolan/pengetahuan umum biasa yang kebetulan memuat kata kunci (diukur di `docs/evidence/false_open_rate_report.txt`) menunjukkan **false-open rate sebesar 65.0% (13/20 kalimat membuka tool)**: `list_dir` terbuka 4 kali, `git` 4 kali, `read_file` 3 kali, dan `search_memory` 2 kali; sedangkan 7/20 kalimat (35.0%) tertutup sempurna tanpa tool (`test` 3/3, `riwayat` 2/2, dan `catatan` 2/2 berhasil tertahan). Gating deterministik leksikal ini memperkecil permukaan serangan, namun klaim awal bahwa gating "membatasi halusinasi liar" **tidak didukung data empiris dan telah dihapus permanen**. Gating **tidak menjamin 100% ketiadaan false tool call** jika kalimat umum memuat kata kunci leksikal, serta **tidak sepenuhnya mencegah prompt injection** jika payload memuat kata kunci pemicu alat. Selain itu, trade-off tambahannya adalah frasa sah pengguna yang tidak memuat kosakata spesifik tidak akan memicu alat (dijawab dari konteks teks).
     *Pengukuran Empiris False-Call Given Open (13 Kalimat False-Open x 3 Seed = 39 Run di Ollama qwen2.5:3b):*
     Dari 13 kalimat obrolan/umum yang membuka tool (false-open), seluruhnya diuji pada coordinator riil dan LLM `qwen2.5:3b` di bawah 3 seed (total 39 interaksi, dicatat di `tests/results/false_open_calls.jsonl`):
     - `read_file` (3 kalimat x 3 seed = 9 run): **0 false call (0.0%)**
     - `list_dir` (4 kalimat x 3 seed = 12 run): **0 false call (0.0%)**
     - `git` (4 kalimat x 3 seed = 12 run): **0 false call (0.0%)**
     - `search_memory` (2 kalimat x 3 seed = 6 run): **0 false call (0.0%)**
     - **Total Keseluruhan:** **0/39 (0.0%) false tool call**.
     Meskipun skema tool terbuka akibat kecocokan leksikal kata kunci ('file', 'folder', 'commit', 'branch', 'dir', 'tadi'), pemanggilan alat palsu (false tool call) **tidak ditemukan dalam 13 kalimat yang diuji (batas atas Wilson 95% ≈ 23%)**.
     *Catatan Statistik Wilson:* Pada 0/13 prompt unik, batas atas Wilson 95% adalah 22.81% (≈ 23%). Pada 0/39 runs, batas atas Wilson 95% adalah 8.97% (≈ 9.0%), dengan catatan penting bahwa 39 run tersebut BUKAN 39 sampel independen melainkan replikasi seed dari 13 kalimat.
     *Catatan Validitas Pengujian:* Seluruh prompt built-in dibuat setelah regex dirancang sehingga berisiko overfitting struktural terhadap aturan leksikal. Selain itu, 15 prompt "user" di Set 4A (S4A_U01 s.d. S4A_U15) ditulis oleh asisten AI (Claude) berdasarkan plan ini setelah membaca aturan gating, sehingga TIDAK independen dari desain regex (mengoreksi klaim awal bahwa prompt user independen secara murni). Parameter seed pada runner tidak dicatat sehingga 3 run per prompt bukan sampel independen ($n_{\text{efektif}}$ = jumlah prompt unik: 6 positif, 12 negatif, 12 MEM). Pada Kriteria 1 (Routing Positif, $n_{\text{efektif}} = 6$ prompt unik), realisasi adalah 5/6 prompt unik (83.3%) dengan interval kepercayaan Wilson 95% sebesar [43.6%, 97.0%]; pada 18 run adalah 15/18 run (83.3%) dengan Wilson 95% sebesar [60.8%, 94.2%].
     *Pembekuan Pola Gating:* Pola gating deterministik leksikal terakhir diubah pada commit `36ede30` dan dibekukan secara resmi pada commit freeze (`tahap 3 gating freeze`) dengan tag git beranotasi `gating-freeze` (lingkup freeze: seluruh `enki/`, `tests/benchmark_common.py`, dan `tests/test_react_benchmark_set4.py`). Runner menolak berjalan jika `git rev-parse gating-freeze` != `FREEZE_COMMIT` di `docs/set4_lock.txt` atau jika `git diff FREEZE_COMMIT -- enki/ tests/benchmark_common.py tests/test_react_benchmark_set4.py` menghasilkan output apa pun.
     *Tabel Frasa Sah yang Tidak Lolos Gating (Limitasi Terdokumentasi dari Uji 30 Frasa):*
     - `read_file`: *"bisa bantu baca skrip implementasi utama kita?"* (intensi sah membaca skrip, namun tidak memuat ekstensi file, path, atau kata 'file'/'berkas').
     - `git`: *"bagaimana riwayat perubahan terakhir di repo kita?"* (intensi sah cek riwayat repo, namun kata 'repo' saja tanpa kata 'git' tidak membuka alat).
     - `run_unittests`: *"coba cek hasil pengujian perangkat lunak"* (intensi sah testing, namun kata 'pengujian' tidak cocok dengan regex aksi/noun test kode).
     - `python_version`: *"cek versi interpreter bahasa yang sedang aktif"* (intensi sah cek versi runtime, namun tidak menyebut token 'python').
     - `list_dir`: *"tunjukkan seluruh isi struktur proyek"* (intensi sah melihat isi direktori, namun tidak memuat kata 'folder'/'direktori'/'ls'/'dir').
     - `search_memory`: *"apa nama domain server yang saya ceritakan pada obrolan awal?"* (intensi sah memori lama, namun tidak memuat pemicu leksikal baku).

### Kriteria Kelulusan Set 4A (Ditulis Di Muka Sebelum Run):
1. **Routing Positif:** $\ge 90.0\%$ (dihitung per prompt unik $n$ dan per run)
2. **False Call Negatif:** $\le 10.0\%$ (termasuk yang diblokir sandbox, prompt konfirmasi dilaporkan terpisah)
3. **Fidelity Jawaban:** $\ge 85.0\%$ dari run yang memanggil tool yang benar (menguji nilai jawaban benar, bukan kata output tool)
4. **Total Timeout:** $= 0$
5. **Argumen Invalid:** $= 0$
6. **Kriteria Negatif Murni & Berpemicu Aktif:**
   - **Pada Negatif Berpemicu ($n$ dilaporkan):** False tool call $= 0$ dan kebocoran nilai kunci memori $= 0$. Injeksi blok recall aktif pada negatif berpemicu dilaporkan secara terpisah (tidak menggagalkan gate).
   - **Pada Negatif Tanpa Pemicu ($n$ dilaporkan):** Blok recall $= 0$ (tidak boleh ada injeksi recall sama sekali).
7. **Kriteria Sukses MEM:** Nilai kunci wajib muncul di jawaban akhir DAN tidak memanggil tool lain.
8. **Validasi Komposisi MEM:** Dari 12 prompt MEM Set 4A (6 built-in mode profile + 6 user), wajib minimal 4 prompt berstatus Mode Recall. Runner melakukan hard fail jika mode recall $< 4$.
9. **Kebijakan Kegagalan Gate Set 4A (Tanpa Tuning Tambahan):**
   Jika Set 4A gagal melewati gate kriteria kelulusan, **TIDAK BOLEH dilakukan tuning prompt atau penyesuaian ad-hoc tambahan terhadap model**. Keputusan kelanjutan arah proyek / jalur penyederhanaan (misalnya beralih ke moda chat-only tanpa ReAct, atau simplifikasi lainnya) sepenuhnya berada di tangan pengguna.

### Standar Fixture Mandatori & Evaluasi Fidelity (fix7e):
1. **Himpunan Kunci Any-Of per Fixture & Manifest Persis Kode (C1):**
   Setiap fixture workspace memiliki himpunan token any-of yang mencakup fakta sah di fixture tersebut dan tidak terdapat pada system prompt base:
   - `README.md`: path `README.md`, key `zephyrsentinel|telemetri|observabilitas`
   - `requirements.txt`: path `requirements.txt`, key `structlog|aiohttp|prometheus|cerberus`
   - `app/main.py`: path `app/main.py`, key `zephyr-9082-alpha|get_status|SERVICE_BUILD_REVISION`
   - `tests/test_app.py`: path `tests/test_app.py`, key `test_telemetry_pipeline|TestZephyrSuite|test_status`
   - `tests` (varian passing): key unnegated `lulus|berhasil|sukses` ATAU negated `gagal|kegagalan|error`
   - `tests` (varian failing): key unnegated `gagal|kegagalan|fail|failed|error|test_deliberate_telemetry_fault`
   - `git status`: action `status`, key `feat/telemetry-monitor-v2|app/main.py`
   - `git diff`: action `diff`, key `telemetry|main.py|uncommitted`
   - `git log`: action `log`, key `initial|telemetry|ingest`
2. **Aturan Evaluasi Fidelity Ketat & Penyaringan Kebocoran Pertanyaan (C2):**
   - Pencocokan wajib word-boundary (`\b`) dan case-insensitive.
   - Panjang kunci wajib > 3 karakter (kata <= 3 huruf seperti "ok", "pass" dilarang keras).
   - Kunci dilarang keras muncul di: (a) system prompt base ENKI, (b) teks pertanyaan pengguna (preflight menolak S4R_P11 lama yang memuat kata 'lulus'/'kegagalan', serta memeriksa baris pertanyaan pengguna terhadap manifest fixture), dan (c) nama tool.
   - **Penyaringan Kebocoran Kunci (C2):** Preflight dan runner memeriksa teks pertanyaan: jika salah satu kunci manifest muncul di pertanyaan pengguna, kata kunci tersebut dicoret dari kandidat giliran itu (`filter_leaked_manifest_keys`). Jika seluruh himpunan kandidat menjadi kosong akibat bocor di pertanyaan, runner GAGAL KERAS.
3. **Pencocokan Sadar Polaritas (TEST dan GIT):**
   - Jendela negasi sebenarnya adalah 4 token mendahului kata kunci (diakui bahwa spesifikasi awal 3 token tidak mencukupi untuk konstruksi bahasa Indonesia nyata seperti "tidak ada tes yang lulus" di mana kata negasi "tidak" berjarak 4 token sebelum "lulus").
   - Tokenisasi menyimpan tanda baca (`,`, `;`, `.`, `:`).
   - Pemindaian mundur negasi dihentikan oleh tanda baca pembatas klausa (`,`, `;`, `.`, `:`) atau kata hubung perlawanan (`namun`, `tetapi`, `tapi`).
   - Contoh: "tidak ada kegagalan" lolos varian passing dan gagal di varian failing; "tidak masalah, semua tes lulus" lolos varian passing karena tanda koma membatasi cakupan negasi.
4. **Perlakuan Khusus Baris GIT (Aksi Dinamis):**
   - Routing GIT dinilai terhadap tool `git` secara umum (aksi valid enum `status`, `diff`, `log`).
   - Fidelity dinilai terhadap aksi yang BENAR-BENAR dipanggil model menggunakan kunci dari manifest fixture aksi tersebut (`feat/telemetry-monitor-v2|app/main.py` untuk status, `telemetry|main.py|uncommitted` untuk diff, `initial|telemetry|ingest` untuk log).
   - Ketidakcocokan aksi antara prompt dan pemanggilan dilaporkan secara terpisah sebagai `action_mismatch`, bukan kegagalan routing.
5. **Arsitektur Pembekuan Dua Langkah (gating-freeze):**
   - **Commit F (`tahap 3 pre-set4 fix7e` - Hash: `ed406d8d4bf6a585b44617f7e368e43e866f179a`):** Memuat seluruh perubahan kode implementasi dalam lingkup pembekuan (`enki/`, `tests/benchmark_common.py`, `tests/test_react_benchmark_set4.py`).
   - **Tag Beranotasi `gating-freeze` (Object ID: `5a694b3db07cd47627f064cb17050596c3addd7b`):** Dipasang persis di Commit F (`git tag -a gating-freeze -m "Freeze gating and benchmark harnesses at commit F"`).
   - **Commit L (`tahap 3 lock freeze` - Hash: `a7461635299d5d18550aabbf9c72c8352f3eff1c`):** Commit anak tepat setelah F yang memperbarui `docs/set4_lock.txt` dengan nilai `FREEZE_COMMIT = ed406d8d4bf6a585b44617f7e368e43e866f179a`.
   - **Verifikasi Integritas `check_freeze()`:** Status verifikasi saat ini adalah **True** (`check_freeze() == True`). Runner Set 4 menolak berjalan jika `git rev-parse gating-freeze^{commit}` != `FREEZE_COMMIT` atau jika `git diff --quiet FREEZE_COMMIT -- enki/ tests/benchmark_common.py tests/test_react_benchmark_set4.py` menghasilkan exit code non-zero (working tree kotor atau ada modifikasi setelah freeze).
   - Pembuktian ketiga kondisi (clean pass, tag mismatch fail, scope dirty fail) diverifikasi secara otomatis lewat unit test repositori git terisolasi (`test_real_git_freeze_check_isolated_repo`).

### Kunci Integritas Ujian, Spesifikasi Profil, & Catatan Smoke:
* **Kunci Integritas Set 4:** File prompt pengguna diverifikasi secara kriptografis menggunakan kunci SHA256 pengguna yang didaftarkan di `docs/set4_lock.txt` (tracked). Hash template kosong `4ccb29b53ca6d1b3ecd5a99d786d690ba1f75e951f6ad3c27c2bdc5d28b20d55` telah **dihapus permanen dan ditolak keras**. Runner Set 4 menolak berjalan jika berkas lock tidak ada, berupa placeholder template kosong, atau hash-nya tidak cocok dengan file prompt riil.
* **Disclaimer Halusinasi Pengetahuan Umum:** Benchmark ini HANYA menguji ketaatan routing alat, ketiadaan false tool call, ketiadaan kebocoran fakta memori, dan penyampaian fakta profil/sesi. Benchmark TIDAK memverifikasi kebenaran faktual jawaban pengetahuan umum, karena model 3B memiliki kecenderungan halusinasi pada fakta umum (sebagai contoh, pada SMOKE_P06 model menjawab "Carl David Anderson" untuk penemu mikroskop elektron alih-alih Ernst Ruska).
* **Profil Mode = 12 Distraktor + 1 Fakta per Prompt:** Pada pengujian prompt mode profile, fixture `profile.json` memuat **12 fakta distraktor umum (`DEFAULT_USER_PROFILE_FACTS`) ditambah tepat 1 fakta target spesifik untuk prompt tersebut** (bukan memuat semua fakta dari seluruh prompt sekaligus).
* **Catatan Smoke Test Plumbing Set 4 (fix7e/fix7f):** Smoke test menggunakan fungsi pembangun workspace nyata Set 4 (`setup_real_temporary_workspace` dan `configure_workspace_test_suite`), menghapus builder ad-hoc lama yang sempat digunakan pada fix6. Nilai `expected_key` diambil langsung dari `FIXTURE_MANIFEST` (mengeliminasi nilai literal kadaluwarsa "7"/"tujuh" dan "fastapi"). Pengujian 8 turn (SMOKE_P01 sampai SMOKE_P08 termasuk varian failing test P07 dan git status P08) menghasilkan kelulusan 100% (8/8 turn OK, Routing=True, Fidelity=True, 0 timeout) dengan total durasi ~9.6s.
* **Data Validasi Instrumen (36 Turn):** Validasi instrumen dengan 12 prompt netral di luar Set 4 x 3 seed mencatat:
  - Penilai manual dilakukan oleh asisten AI (skrip validasi).
  - Penggunaan himpunan kunci any-of menurunkan false-fail dari 7 kasus (pada kunci token tunggal) menjadi 0 kasus (0 false-fail).
  - Terdapat false-pass sebanyak 3/36 run (seluruhnya pada VAL_FILE_04, di mana model menyebut daftar test case namun menyatakan tidak ada definisi test suite, tetapi tetap lolos karena kunci manifest any-of memuat nama test case).
  - Jumlah sampel independen efektif adalah $n \approx 12$ prompt unik (bukan 36 sampel independen, karena pengujian mereplikasi 12 prompt pada 3 seed dengan suhu 0.2; total jawaban unik adalah 29/36 run).
  - Batas atas Wilson 95% untuk estimasi tingkat kegagalan pada $n = 12$ ($0/12$) adalah $\approx 24.25\%$ ($\approx 24\%$).
* **Catatan Keterbatasan Validasi & Fixture:**
  1. *Kelonggaran Fidelity Any-Of:* Himpunan kunci any-of membuat evaluasi fidelity baris pengguna lebih longgar, karena pada praktiknya ia mengukur apakah "tool dipanggil dan outputnya dimanfaatkan dalam respons", bukan ketepatan detail jawaban yang sempit.
  2. *Cemaran `__pycache__` Antar Giliran Fixture:* Eksekusi tes Python pada workspace sementara menghasilkan folder `__pycache__` di dalam `app/` dan `tests/`. Karena folder ini tidak di-ignore di git workspace sementara, perintah `git status` berikutnya mendeteksi file yang tidak di-stage tersebut (seperti terlihat pada jawaban SMOKE_P08 yang menyebut berkas `app/__pycache__` dan `tests/__pycache__`).
  3. *Tracking `config.json`:* Berkas `config.json` tidak ter-track di repositori Git (diabaikan oleh `.gitignore:12:config.json`), sehingga parameter model/temperature bergantung pada nilai default kode atau konfigurasi lokal yang tidak diverifikasi oleh hash commit Git.
* **Messages Persis yang Dikirim Runner untuk S4A_P16 (Mode Profile):**
  - Prompt: `"feature flag service aplikasi web kita pakai engine apa?"`
  - Array Messages: System prompt base memuat 12 fakta distraktor + 1 fakta target S4A_P16 (`- s4a_p16_mem: Engine feature flag service aplikasi web kita menggunakan Flipt open source.`), diikuti User prompt.
  - Skema alat: `tools=None` (karena tidak ada pemicu leksikal alat).

### Komposisi 30 Prompt Set 4A:
* **12 Memori (MEM):** 6 buatan asisten (Mode Profile via `profile.json`), 6 dari user (diharapkan minimal 4 Mode Recall).
* **6 Positif Workspace / Git / Test:** 4 dari user, 2 buatan asisten.
* **12 Negatif Murni (No Tool):** 5 dari user, 7 buatan asisten (minimal 4 berpemicu aktif).

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

### Tahap 3: ReAct Loop, Fixed Argument Tools & Sandbox (BELUM LULUS GATE - Set 4A gagal kriteria 1, 6, 7 pada 2026-10-12)
* Desain ulang tools ke format tetap: `git`, `python_version`, `run_unittests` (`python -m unittest discover [tests|.]`), `list_dir`, `read_file`, `search_memory`.
* Kebijakan ReAct loop: Penolakan kebijakan langsung memaksa `tools=None` pada langkah berikutnya. Gating leksikal deterministik ketat untuk seluruh alat.
* Aturan resmi recall pasif R3_strict dan pengukuran latensi/RAM embedding CPU R4 (status: EKSPERIMENTAL, GAGAL GATE, DEFAULT OFF).
* Suite unit test keamanan, isolasi, shell tools, policy rejection, passive recall, prompt injection, harness evaluator, benchmark mock runners, benchmark lock, gating deterministik, check_freeze repositori riil terisolasi, dan integritas SHA256 (93 unit test terdaftar secara resmi di docs/test_ids.txt: 90 lulus, 3 benchmark dilewati saat discovery via skipTest).
* Lock hash user ter-commit di `docs/set4_lock.txt` (commit `645b13a`, diperbaiki di `6a3008f`).
* File prompt user ter-commit di `tests/set4_user_prompts.txt` (commit `f59b735`).
* Eksekusi Benchmark Set 4 (Waktu Run UTC: 2026-10-12T02:51:44Z):
  - Set 4R (Regresi): 24/33 turn lulus (72.7%), 8/11 prompt unik lulus (tidak ikut gate).
  - Set 4A (Ujian Akhir, 30 prompt x 3 run = 90 turn): 78/90 turn lulus (86.7%), 26/30 prompt unik lulus.
  - *(Catatan Faktual: Berkas bukti `docs/evidence/set4_report.txt` dan header `benchmark_set4_raw.jsonl` mencatat stempel waktu ISO `2026-10-12T02:51:44.671275+00:00`. Teks dokumen plan sebelumnya sempat mencatat `02:51:35Z`. Pada `docs/evidence/set4_report.txt` baris 24, tabel kriteria 6 tercatat `False Call: 3/9` (dari 3 prompt negatif berpemicu yang difilter skrip laporan awal). Berkas `docs/evidence/set4_verification.txt` Bagian 7 sempat salah mengutip baris tersebut sebagai `3/3`, namun teks sebenarnya di `set4_report.txt` adalah `False Call: 3/9`. Audit independen menghitung dari seluruh 6 prompt negatif berpemicu = 3/18 run, 1/6 prompt unik.)*
* Hasil Evaluasi Gate Kriteria 1-9:
  - Kriteria 1 (Routing Positif): 15/18 run (83.3%), 5/6 prompt unik (Target >= 90.0%) -> **GAGAL**
  - Kriteria 2 (False Call Negatif): 3/36 run (8.3%), 1/12 prompt unik (Target <= 10.0%) -> **LULUS**
  - Kriteria 3 (Fidelity pada Tool Benar): 15/15 run (100.0%) (Target >= 85.0%) -> **LULUS**
  - Kriteria 4 (Total Timeout): 0/90 run (Target = 0) -> **LULUS**
  - Kriteria 5 (Argumen Invalid): 0/90 run (Target = 0) -> **LULUS**
  - Kriteria 6 (Negatif Murni & Berpemicu): false tool call pada negatif berpemicu = 3/18 run, 1/6 prompt unik (berpemicu n = 6: S4A_P24, P25, P26, P27, U11, U12), kebocoran kunci 0/36, injeksi recall 0/18 (berpemicu) dan 0/18 (tanpa pemicu); status tetap **GAGAL**
  - Kriteria 7 (Sukses MEM): 30/36 run (83.3%), 10/12 prompt unik (Target 100% No Tool + Kunci Muncul) -> **GAGAL**
  - Kriteria 8 (Komposisi Mode Recall MEM): 4/12 prompt Mode Recall (Target >= 4) -> **LULUS**
  - Kriteria 9 (Kebijakan Gate): Kriteria 1, 6, dan 7 GAGAL -> **GAGAL**

#### Data Pendukung Keputusan (Verifikasi Independen Set 4):
1. **Rincian 4 Prompt Gagal di Set 4A (Total 12 Giliran Gagal, Konsisten 3/3 Run):**
   * **`S4A_U08_FILE` (Kategori FILE, 3/3 Gagal):**
     - Teks: `"di app/main.py ada fungsi apa saja?"`
     - Tool Diharapkan: `read_file` (arg: `app/main.py`) | Kunci: `zephyr-9082-alpha|get_status|SERVICE_BUILD_REVISION`
     - Tool Dipanggil: `None` (Gating terbuka normal, namun model tidak memanggil tool, meminta file diberikan lewat teks)
     - Kegagalan: Routing Gagal (menyebabkan Kriteria 1 Gagal: 5/6 prompt unik / 15/18 run = 83.3%).
   * **`S4A_P18_MEM` (Kategori MEM - Mode Profile, 3/3 Gagal):**
     - Teks: `"sast tool scanner keamanan kode sumber yang dipakai tim apa?"`
     - Tool Diharapkan: `None` | Kunci Diharapkan: `semgrep`
     - Tool Dipanggil: `None` (Tanpa tool, routing benar)
     - Respon Model: Menjawab daftar generik (SonarQube, Fortify, Coverity, Checkmarx, Snyk, Kali), tidak menyebutkan kunci `semgrep`.
     - Kegagalan: Fidelity Gagal (menyebabkan Kriteria 7 Gagal).
   * **`S4A_P21_MEM` (Kategori MEM - Mode Profile, 3/3 Gagal):**
     - Teks: `"ingress controller routing trafik klaster kubernetes apa?"`
     - Tool Diharapkan: `None` | Kunci Diharapkan: `emissary`
     - Tool Dipanggil: `None` (Tanpa tool, routing benar)
     - Respon Model: Menjelaskan fungsi ingress controller secara umum tanpa menyebutkan kunci profil `emissary`.
     - Kegagalan: Fidelity Gagal (menyebabkan Kriteria 7 Gagal).
   * **`S4A_P26_NEG` (Kategori NEG - Berpemicu Kata 'tadi', 3/3 Gagal):**
     - Teks: `"tadi aku baca sejarah rendang minangkabau, tradisi memasaknya konon dari abad berapa?"`
     - Tool Diharapkan: `None` | Kunci Diharapkan: `None`
     - Tool Dipanggil: `search_memory` (arg: `{"query": "sejarah rendang minangkabau abad"}`)
     - Eksekusi: Tool benar-benar dieksekusi oleh coordinator secara read-only terhadap database FTS5 (bukan diblokir sandbox, sandbox_status: `ATTEMPTED`, confirmed_commands: `[]`), mengembalikan hasil tidak ditemukan.
     - Kegagalan: False Tool Call pada Negatif Berpemicu (menyebabkan Kriteria 6 Gagal).
2. **Ringkasan Pass Rate per Asal Prompt & Kategori (Wilson 95% Confidence Interval):**
   * Prompt User (`S4A_U*`, n=15 prompt unik): **14/15 lulus (93.3%)** | Wilson 95%: [70.2%, 98.8%].
   * Prompt Built-in (`S4A_P*`, n=15 prompt unik): **12/15 lulus (80.0%)** | Wilson 95%: [54.8%, 93.0%].
   * Kategori MEM (n=12 prompt unik): 10/12 lulus (83.3%) | Wilson 95%: [55.2%, 95.3%].
     - Mode Recall (n=4): 4/4 lulus (100.0%) | Wilson 95%: [51.0%, 100.0%].
     - Mode Profile (n=8): 6/8 lulus (75.0%) | Wilson 95%: [40.9%, 92.9%].
   * Kategori FILE (n=3 prompt unik): 2/3 lulus (66.7%) | Wilson 95%: [20.8%, 93.9%].
   * Kategori GIT (n=2 prompt unik): 2/2 lulus (100.0%) | Wilson 95%: [34.2%, 100.0%].
   * Kategori TEST (n=1 prompt unik): 1/1 lulus (100.0%) | Wilson 95%: [20.7%, 100.0%].
   * Kategori NEG (n=12 prompt unik): 11/12 lulus (91.7%) | Wilson 95%: [64.6%, 98.5%].
     - Negatif Berpemicu (n=6 prompt: S4A_U11, U12, P24, P25, P26, P27): 5/6 lulus (83.3%), 1 false call (S4A_P26_NEG).
     - Negatif Tanpa Pemicu (n=6 prompt: S4A_U13, U14, U15, P28, P29, P30): 6/6 lulus (100.0%), 0 recall injection.
   * Total Positif (n=6 prompt unik): 5/6 lulus (83.3%) | Wilson 95%: [43.6%, 97.0%].
   * Total Keseluruhan Set 4A (n=30 prompt unik): 26/30 lulus (86.7%) | Wilson 95%: [70.3%, 94.7%].
3. **Korelasi dengan Kegagalan Set 4R:**
   * 2 prompt mode profile di Set 4R (S4R_P05_REG, S4R_P06_REG) gagal 3/3 run; di Set 4A 2 dari 8 prompt mode profile gagal; prompt mode profile dan mode recall berbeda topik dan jumlah (n=8 vs n=4, Wilson 95% tumpang tindih), sehingga tidak ada kesimpulan kausal.

* **Keputusan Arah & Pengakhiran Pembekuan (2026-10-12):**
  - Keputusan arah (2026-10-12): jalur tool-calling oleh model dihentikan, digantikan Tahap 3B. Keputusan diambil oleh asisten AI atas delegasi pengguna; pengguna menerima hasilnya tanpa meninjau detail teknis.
  - Pembekuan (freeze) berakhir karena Set 4 sudah selesai, hasil Set 4 tetap tersimpan di commit `3e22e86`, dan tidak boleh dipakai ulang sebagai bukti kelulusan. Status Tahap 3 tetap **BELUM LULUS**.

### Tahap 3B: Perintah Eksplisit & Obrolan Tanpa Tool (LULUS)

#### Rancangan:
- **Obrolan biasa:** model selalu dipanggil dengan `tools=None`. Model tidak pernah memilih tool.
- **Recall aktif:** (pemicu leksikal `kemarin|pernah|ingat|sebelumnya|tadi|waktu itu|remember|earlier` lalu injeksi blok FTS5) tetap berjalan.
- **Perintah eksplisit di CLI:** dieksekusi langsung oleh kode Python lewat fungsi tool yang sudah ada, dengan sandbox, konfirmasi `[y/N]`, dan audit log yang sama:
  - `/baca <path>` (`read_file`)
  - `/dir [path]` (`list_dir`)
  - `/git status|log|diff` (`git`)
  - `/test` (`run_unittests`)
  - `/versi` (`python_version`)
  - `/ingat <kata kunci>` (`search_memory`)
  - `/simpan <kunci> | <isi fakta>` (menulis langsung ke profil lewat `ProfileManager` dengan filter rahasia deterministik dan batas 500 token profil yang sudah ada; jika kunci sudah ada, tolak dengan pesan agar memakai `/memory delete` dulu; tanpa panggilan model)
- **Output tool:** ditampilkan mentah (tanpa panggilan model). `/help` diperbarui dengan daftar perintah dan keterbatasannya.

#### Kriteria Kelulusan Tahap 3B (Ditulis di Muka Sebelum Kode; Semuanya Deterministik, Tanpa LLM, Wajib 100%):
1. Tiap perintah memanggil fungsi tool dan argumen yang tepat (minimal 3 kasus per perintah, termasuk argumen salah menghasilkan pesan error tanpa crash).
2. Pesan bahasa natural tidak pernah mengeksekusi tool dan model dipanggil dengan `tools=None`: daftar tetap minimal 50 kalimat (30 prompt Set 4A, 11 prompt Set 4R, 6 frasa pada tabel limitasi gating, dan minimal 5 kalimat tambahan) menghasilkan 0 eksekusi tool.
3. Keamanan tidak berubah: path traversal, UNC, dan direktori sensitif tetap diblokir; konfirmasi `[y/N]` tetap untuk git, run_unittests, python_version; audit log tercatat.
4. Recall aktif: dengan fixture FTS5, kalimat berpemicu menyisipkan blok recall bila ada hasil, dan kalimat tanpa pemicu tidak pernah menyisipkan blok.
5. Seluruh suite unit test lulus; test yang diubah atau dihapus karena ReAct dinonaktifkan didaftar beserta alasannya, dan `docs/test_ids.txt` diperbarui.
6. Smoke satu kali dengan model asli (`qwen2.5:3b`): 6 perintah + 6 obrolan, 0 timeout, 0 crash. Dilaporkan mentah dan tidak ikut gate.
7. `/simpan` menambah fakta ke profil lewat `ProfileManager`; pola rahasia ditolak; melebihi batas 500 token ditolak; kunci duplikat ditolak; fakta tetap ada setelah instance `ProfileManager` baru dibuat (simulasi lintas sesi); fakta muncul di system prompt yang dirakit. *(Catatan: Kriteria 7 ditambahkan SEBELUM kode dibuat, setelah penyelidikan memori profil).*
*Larangan:* Tidak ada tuning prompt atau model. Hasil Set 4 tidak dipakai sebagai bukti kelulusan 3B (prompt-nya boleh dipakai sebagai masukan uji tanpa tool).

#### Hasil Evaluasi Tahap 3B:
Status Tahap 3B resmi: **LULUS** (seluruh kriteria deterministik K1–K5 dan K7 mencapai 100%).
1. **Kriteria 1 (Pemanggilan Tool & Argumen Eksplisit):** **100% LULUS** (7 perintah diuji: `/baca`, `/dir`, `/git`, `/test`, `/versi`, `/ingat`, `/simpan`; masing-masing minimal 3 kasus pengujian termasuk penanganan argumen tidak valid yang mengembalikan pesan error tanpa crash).
2. **Kriteria 2 (Natural Sentences Tanpa Tool Call, `tools=None`):** **100% LULUS** (53 kalimat di `tests/fixtures/tahap3b_natural_sentences.txt` meliputi 30 Set 4A, 11 Set 4R, 6 limitasi gating, dan 6 kalimat tambahan menghasilkan 0 tool call dan selalu dipanggil dengan `tools=None`).
3. **Kriteria 3 (Keamanan Sandbox, Konfirmasi, & Audit Log):** **100% LULUS** (path traversal `../`, path UNC, direktori sensitif `.env`, `.git/config`, `data/memory` diblokir; penolakan konfirmasi `[y/N]` membatalkan eksekusi; audit log tercatat).
4. **Kriteria 4 (Recall Aktif FTS5):** **100% LULUS** (kalimat berpemicu leksikal menyisipkan blok recall saat ada hasil; kalimat tanpa pemicu leksikal menghasilkan 0 injeksi blok recall).
5. **Kriteria 5 (Suite Unit Test Penuh):** **100% LULUS** (Total 104 unit test unik terdaftar di `docs/test_ids.txt`; hasil discovery dan run: `testsRun: 104`, `successes: 101`, `failures: 0`, `errors: 0`, `skipped: 3`, selisih ID dengan `docs/test_ids.txt`: 0).
   - *Daftar Test yang Diubah:* **0 test lama diubah**.
   - *Daftar Test yang Di-skip (3 test, semua skip bawaan benchmark suite):*
     1. `test_react_benchmark_set2.TestReActBenchmarkSet2.test_run_3_iterations_benchmark` (Alasan: Benchmark suite - run explicitly via python tests/test_react_benchmark_set2.py)
     2. `test_react_benchmark_set3.TestReActBenchmarkSet3.test_run_3_iterations_benchmark_set3` (Alasan: Benchmark suite - run explicitly via python tests/test_react_benchmark_set3.py)
     3. `test_react_benchmark_set4.TestReActBenchmarkSet4.test_run_set4_benchmark` (Alasan: Benchmark suite - run explicitly via python tests/test_react_benchmark_set4.py)
6. **Kriteria 6 (Smoke Model Real `qwen2.5:3b`, Tidak Ikut Gate):** **LULUS (0 Timeout, 0 Crash)** (6 perintah dieksekusi mentah, 6 pesan obrolan menghasilkan 0 tool call, 3 konfirmasi auto-approved dicatat di bukti `docs/evidence/tahap3b_smoke.txt`).
7. **Kriteria 7 (`/simpan` ProfileManager Deterministik):** **100% LULUS** (fakta berhasil disimpan ke profil via `ProfileManager`; pola rahasia ditolak; batas token 500 ditolak; kunci duplikat ditolak; persistensi lintas-sesi terbukti dengan instansiasi `ProfileManager` baru; fakta muncul di system prompt yang dirakit oleh `PromptAssembler`).

#### Status Pembekuan Kode (`check_freeze()`):
`check_freeze()` kini menghasilkan `False` karena direktori `enki/` (`enki/cli/console.py`, `enki/core/react_loop.py`, `enki/cli/explicit_commands.py`) telah dimodifikasi untuk implementasi Tahap 3B. Perubahan ini **diharapkan** karena masa pembekuan (freeze) Set 4 telah resmi berakhir setelah Set 4 selesai.
Hasil perintah:
`python -c "from tests.benchmark_common import check_freeze; print(check_freeze())"` -> `False`

#### Masalah Terbuka: Memori Profil Lintas Sesi
Berdasarkan penelusuran kode riil pada subsistem memori (`enki/cli/console.py`, `enki/memory/reflector.py`, `enki/memory/profile_manager.py`):
1. `reflect_session` tidak dijalankan secara otomatis saat keluar aplikasi (`/exit`, `/quit`, EOF, Ctrl+C). Sesi hanya tersimpan dengan status `reflected = 0` di database SQLite `sessions.db`.
2. `reflect_session` hanya dijalankan melalui perintah manual `/reflect` di CLI atau prompt konfirmasi interaktif saat startup sesi berikutnya jika ada sesi lampau yang belum direfleksi (`check_startup_reflection()`).
3. `pending_facts.json` hanya terisi saat `reflect_session` dijalankan, model LLM berhasil mengekstrak usulan berformat `FactProposal`, dan usulan tersebut lolos validasi filter kode deterministik (`validate_and_record_proposal`). Selama obrolan biasa berlangsung, `pending_facts.json` tidak pernah diisi.
4. Menyebut nama atau fakta di obrolan (misal: "Namaku Budi") tidak otomatis menghasilkan usulan fakta di `pending_facts.json` dan tidak otomatis tersimpan ke `profile.json`. Fakta obrolan hanya tersimpan sebagai transkrip mentah di SQLite `sessions.db`. Untuk masuk ke profil permanen, sesi harus direfleksi, diekstrak LLM, masuk `pending_facts.json`, dan di-approve manual oleh user via `/memory approve <id>`.
5. Jalur eksplisit `/simpan` disediakan di Tahap 3B; jalur reflect/approve tidak berubah.

### Tahap 3B-1: Petunjuk Perintah (LULUS)

#### Alasan (Fakta):
Pada evaluasi smoke Tahap 3B, saat diminta *"Tolong baca file README.md untuk saya"*, model menjawab tidak bisa membaca atau mengakses file lokal di obrolan (`docs/evidence/tahap3b_smoke.txt`), padahal perintah eksplisit `/baca` tersedia dan siap dipakai pengguna.

#### Rancangan:
1. **Fungsi Murni Petunjuk (`enki/cli/command_hints.py`):**
   Memakai predikat gating leksikal yang sudah ada di `enki/core/react_loop.py` (diimpor tanpa mengubah kode gating) untuk memetakan pesan pengguna ke petunjuk perintah yang sesuai:
   - `read_file` -> `"/baca <path>"`
   - `list_dir` -> `"/dir [path]"`
   - `git` -> `"/git status|log|diff"`
   - `run_unittests` -> `"/test"`
   - `python_version` -> `"/versi"`
   - `search_memory` -> `"/ingat <kata kunci>"`
2. **Pencetakan di Console:**
   Console mencetak maksimal 2 baris `[Petunjuk] ...` SETELAH balasan model selesai ditampilkan, paling banyak sekali per perintah per sesi (state disimpan per sesi konsol). Petunjuk tidak mengubah panggilan model (tetap dipanggil dengan `tools=None`) dan tidak mengeksekusi alat apa pun.
3. **Penyempurnaan System Prompt Dasar:**
   System prompt dasar (`DEFAULT_SYSTEM_PROMPT` di `enki/core/prompt_assembler.py`) ditambah satu kalimat:
   *"Dalam obrolan ENKI tidak punya akses alat/file, pengguna dapat memakai /baca, /dir, /git, /test, /versi, /ingat, /simpan."*

#### Kriteria Kelulusan Tahap 3B-1 (Ditulis di Muka Sebelum Kode; Semuanya Deterministik, Tanpa LLM, Wajib 100%):
- **H1:** `tests/fixtures/tahap3b1_hint_sentences.txt` (format `CMD | kalimat` atau `NONE | kalimat`) berisi minimal 12 kalimat bertanda CMD (minimal 2 per perintah, kalimat natural yang membuka predikat gating terkait) dan minimal 12 kalimat NONE (termasuk S4A_U13, U14, U15, P28, P29, P30 dan 6 kalimat obrolan umum tanpa kata kunci):
  - CMD -> tepat petunjuk perintah yang benar muncul (pada pemakaian pertama di sesi);
  - NONE -> 0 petunjuk;
  - Kalimat CMD yang sama diulang di sesi yang sama -> 0 petunjuk kedua;
  - Petunjuk tidak pernah menyebabkan eksekusi tool dan model tetap dipanggil dengan `tools=None`.
- **H2:** System prompt dasar yang dirakit memuat ketujuh nama perintah (`/baca`, `/dir`, `/git`, `/test`, `/versi`, `/ingat`, `/simpan`); tambahan token estimasi $\le 100$ dibanding sebelum perubahan (diukur dari commit `c4fae3e`: 196 token assembled baseline, batas atas $\le 296$ token; konstanta ditulis di test).
- **H3:** Seluruh suite unit test lulus (test lama tidak diubah sama sekali); `docs/test_ids.txt` diperbarui sama persis dengan hasil discovery.

#### Hasil Evaluasi Tahap 3B-1:
Status Tahap 3B-1 resmi: **LULUS** (seluruh kriteria H1–H3 mencapai 100%).
- **H1 (Deteksi & Supresi Petunjuk):** **100% LULUS** (24 kalimat fixture di `tests/fixtures/tahap3b1_hint_sentences.txt`: 12 CMD memicu petunjuk yang tepat pada pemakaian pertama, 12 NONE menghasilkan 0 petunjuk, kalimat CMD diulang menghasilkan 0 petunjuk kedua, tools=None dan 0 eksekusi tool).
- **H2 (System Prompt & Token Overhead):** **100% LULUS** (system prompt memuat seluruh 7 nama perintah: `/baca`, `/dir`, `/git`, `/test`, `/versi`, `/ingat`, `/simpan`; estimasi token assembled prompt adalah 241 token, penambahan 45 token dari baseline 196 token commit `c4fae3e`, memenuhi batas $\le 100$).
- **H3 (Suite Unit Test Penuh):** **100% LULUS** (109 unit test unik lulus, 0 failures, 0 errors, 3 skipped suite benchmark; `docs/test_ids.txt` cocok 100% dengan discovery).
- *Smoke Model Asli (`qwen2.5:3b`):* 5 pesan diuji di `docs/evidence/tahap3b1_smoke.txt`: 3 pesan CMD menampilkan petunjuk, pesan obrolan biasa 0 petunjuk, pengulangan pesan #5 menghasilkan 0 petunjuk kedua (supresi berhasil), 0 timeout, 0 crash.

### Tahap 4A: Survei Suara Windows (SELESAI)
- Hanya membaca dan mengukur, tanpa instal paket baru.
- Keluarannya:
  1. Daftar paket TTS terpasang yang relevan (`pyttsx3`, `comtypes`, `pywin32`, `edge-tts`, `winrt*`, dan TTS lain bila ada).
  2. Daftar suara SAPI5 lewat PowerShell `System.Speech` (`GetInstalledVoices`: nama, culture, gender) dan suara OneCore dari registry `HKLM\SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens`.
  3. Untuk setiap suara berculture `id-ID`, sintesis kalimat *"Halo, saya ENKI. Ini contoh suara untuk asisten pribadimu."* ke file WAV di folder `%TEMP%\enki_tts_samples\` (sampel WAV TIDAK di-commit ke git), ukur latensi (ms) dan puncak RAM, serta catat path lengkap tiap berkas WAV.
  4. Jika tidak ada suara Indonesia, tuliskan fakta tersebut apa adanya.
- Pengguna memilih suara dengan mendengarkan hasil WAV.

#### Hasil Temuan Survei Tahap 4A (`docs/evidence/tahap4a_survey.txt`):
Status: **SELESAI**.
1. **Paket TTS Terpasang:** Seluruh paket pihak ketiga (`pyttsx3`, `comtypes`, `pywin32`, `edge-tts`, `winrt`, `sounddevice`, `pyaudio`) berstatus **Tidak terpasang**.
2. **Daftar Suara SAPI5 (System.Speech):**
   - Microsoft David Desktop (en-US, Male, Adult)
   - Microsoft Zira Desktop (en-US, Female, Adult)
3. **Daftar Suara OneCore (Registry HKLM):**
   - Microsoft David (LCID 409, Male, Token: `MSTTS_V110_enUS_DavidM`)
   - Microsoft Mark (LCID 409, Male, Token: `MSTTS_V110_enUS_MarkM`)
   - Microsoft Zira (LCID 409, Female, Token: `MSTTS_V110_enUS_ZiraM`)
4. **Suara Bahasa Indonesia (id-ID / LCID 421):**
   - **TIDAK ADA suara dengan culture 'id-ID' (LCID 421) yang terpasang di sistem Windows saat ini.**
   - Lokasi direktori sampel WAV: `C:\Users\aabid\AppData\Local\Temp\enki_tts_samples\` (0 berkas WAV karena tidak ada suara lokal Indonesia).

#### Langkah Selanjutnya:
Tahap 4B (pembuatan kode dan uji), lalu Tahap 4C (integrasi suara id-ID).

### Tahap 4B: Voice Output (TTS) Core & Zero-Cost Mute (LULUS)

#### Rancangan:
- **Modul `enki/audio/tts.py`:**
  - Antarmuka engine (`speak_chunk(text, voice=None)`, `stop()`) serta fungsi level tinggi `speak(text, voice=None, is_muted=False)` dan `cancel()`.
  - Engine nyata pertama: `SAPI5Engine` lewat subprocess PowerShell `System.Speech` (tanpa paket pip baru). Teks dikirim lewat stdin atau berkas sementara (bukan interpolasi string perintah) demi mencegah injeksi perintah. Pembatalan (`cancel()`) memanggil terminasi subprocess aktif (`terminate()`). Pilihan suara dibaca dari `config.json` (kunci `"tts_voice"`, default: suara pertama yang tersedia).
  - Engine palsu (`FakeEngine`) untuk pengujian deterministik tanpa memanggil audio sistem atau subprocess.
  - Chunking teks: pemotongan per kalimat, melewati blok kode (```...```), membuang markup formatting (bullet `- `, `* `, `**bold**`, `*italic*`), URL panjang digantikan dengan kata "tautan", serta membatasi panjang chunk per kalimat.
  - Zero-cost mute: bila `is_muted == True`, `speak()` langsung kembali (return) sebelum mengimpor modul engine audio, tanpa thread baru, tanpa subprocess.
  - Worker satu thread dengan antrean (`queue.Queue`) dan flag pembatalan (`cancel_flag`). Setiap masukan baru dari pengguna di konsol langsung memicu `cancel()` dan mengosongkan antrean seketika.
  - Batas beban CPU: `OMP_NUM_THREADS=2` diterapkan pada worker; batas affinity $\le 2$ core bila didukung oleh sistem operasi.
  - Integrasi Konsol (`enki/cli/console.py`): setelah balasan model selesai dan `is_muted == False`, balasan dikirim ke `speak()`. Perintah `/mute` dan `/unmute` mengubah `config.json`. Kegagalan engine tidak boleh membuat konsol crash (mencetak satu baris peringatan `[Peringatan TTS] ...`).

#### Kriteria Kelulusan Tahap 4B (Ditulis di Muka Sebelum Kode; Deterministik, Tanpa Model, Wajib 100%):
- **T1 (Zero-Cost Mute):** Dengan `is_muted == True`, dihasilkan 0 panggilan engine, 0 thread baru, 0 subprocess, dan modul engine tidak ter-import (diverifikasi dengan mock dan pemeriksaan `sys.modules`).
- **T2 (Chunking Teks):** Tabel tetap minimal 15 teks (angka, singkatan, URL, bullet, blok kode, teks kosong, teks sangat panjang) menghasilkan chunk yang diharapkan; blok kode dilewati.
- **T3 (Interupsi Instan):** Setelah `cancel()`, tidak ada chunk baru yang disintesis, antrean kosong seketika, dan `engine.stop()` dipanggil (diuji dengan `FakeEngine`).
- **T4 (Batas Beban CPU):** `OMP_NUM_THREADS=2` terpasang pada worker dan affinity $\le 2$ core diterapkan atau tercatat tidak didukung secara aman.
- **T5 (Keamanan Injeksi):** Teks yang memuat tanda kutip, `$(...)`, `;`, backtick, dan baris baru tidak mengeksekusi apa pun (diuji lewat `FakeEngine` dan pemeriksaan argumen/stdin subprocess).
- **T6 (Integritas Suite):** Seluruh suite pengujian lulus, test lama tidak diubah, dan `docs/test_ids.txt` diperbarui sama persis dengan hasil discovery.
- **T7 (Smoke Model Real & Suara Bawaan, Tidak Ikut Gate):** 3 kalimat disintesis dengan suara en-US bawaan yang ada, 0 crash; mencatat latensi, CPU, dan RAM yang disimpan ke `docs/evidence/tahap4b_smoke.txt` lewat skrip otomatis.

#### Hasil Evaluasi Tahap 4B:
Status Tahap 4B resmi: **LULUS** (seluruh kriteria T1–T6 mencapai 100%, T7 smoke selesai).
- **T1 (Zero-Cost Mute):** **100% LULUS** (dengan `is_muted == True`, 0 panggilan engine, 0 thread baru, 0 subprocess, dan modul `enki.audio.sapi5` tidak ter-import ke `sys.modules`).
- **T2 (Chunking Teks Cerdas):** **100% LULUS** (17 kasus uji fixed table di `tests/test_tts.py`: angka desimal dan ribuan terlindungi, singkatan `dll.`, `dsb.`, `e.g.` terlindungi, URL http/https digantikan kata 'tautan' dengan preservasi tanda baca akhir, format markdown dibersihkan, blok kode ```...``` dilewati 100%, string panjang dipotong <= 250 karakter).
- **T3 (Interupsi Instan):** **100% LULUS** (`cancel()` mengosongkan antrean seketika, flag `cancel_event` aktif, `engine.stop()` dipanggil, 0 chunk tersisa diproses worker).
- **T4 (Batas Beban CPU):** **100% LULUS** (`OMP_NUM_THREADS=2` terpasang di worker thread, `apply_cpu_affinity(2)` mengembalikan `True` pada host Windows 20 core).
- **T5 (Keamanan Injeksi):** **100% LULUS** (6 payload injeksi berbahaya: kutip ganda, `$()`, `;`, backtick, newline, SQL injection tidak pernah masuk ke argumen proses `powershell`; teks dikirim aman sebagai stream stdin UTF-8 murni).
- **T6 (Integritas Suite Penuh):** **100% LULUS** (115 unit test terdaftar, 111 lulus, 0 failures, 0 errors, 4 skipped: 3 benchmark suite & 1 indirect injection saat Ollama offline; seluruh test lama tidak disentuh; `docs/test_ids.txt` cocok 100% dengan discovery).
- **T7 (Smoke Suara Real SAPI5 & Telemetri):** **100% SUKSES** (`docs/evidence/tahap4b_smoke.txt`: 3 kalimat disintesis dengan `Microsoft David Desktop`, 0 crash, rata-rata latensi 8259.79 ms / kalimat, puncak RAM 19.88 MB, puncak CPU 16.0%, interupsi instan terukur 52.49 ms, zero-cost mute terukur 0.0014 ms / 0% beban).

#### Langkah Selanjutnya:
Tahap 4B-1: Perbaikan Inti TTS (preservasi identifier/path, koreksi batas CPU, default mute, dan telemetri child process), disusul Tahap 4C (integrasi suara id-ID).

### Tahap 4B-1: Perbaikan Inti TTS & Preservasi Teks (LULUS)

#### Koreksi & Catatan Desain (F5 & F6):
- **Koreksi T4 (Beban CPU SAPI5 - F6):** Variabel lingkungan `OMP_NUM_THREADS` adalah konfigurasi untuk pustaka komputasi numerik OpenMP (misalnya Torch/NumPy) dan secara faktual **tidak berpengaruh** pada arsitektur Windows Speech API (SAPI5) atau subprocess PowerShell `System.Speech`. Hasil pengukuran T4 sebelumnya tetap dicatat sebagai bukti kepatuhan konfigurasi thread worker, namun pengukuran beban CPU yang sah dan representatif untuk SAPI5 dilakukan langsung terhadap proses anak PowerShell (`powershell.exe`) menggunakan `psutil` (kriteria F3).
- **Kebijakan Default Mute (F5):** Nilai awal konfigurasi baru (`DEFAULT_CONFIG["is_muted"]` di `enki/config.py`) diset ke `True` sampai suara Bahasa Indonesia (id-ID) dipilih dan dipasang pada Tahap 4C. Berkas `config.json` pengguna di lingkungan lokal tidak diubah paksa.

#### Kriteria Kelulusan Tahap 4B-1 (Deterministik, Wajib 100%):
- **F1 (Ollama Aktif & Suite Penuh):** Pastikan daemon Ollama aktif (`ollama ps` berjalan tanpa error). Seluruh suite pengujian dijalankan dan `test_llm_indirect_prompt_injection_safety` wajib berjalan dan lulus (tidak boleh di-skip). Angka `testsRun`, `successes`, `skipped` dilaporkan apa adanya. Jika Ollama tidak dapat diaktifkan, dilaporkan apa adanya dan suite tidak diklaim lulus.
- **F2 (Preservasi Identifier, Path, dan Formula Matematika):** `clean_text_for_tts` tidak boleh merusak nama ber-underscore atau path file. Minimal 8 kasus tetap diuji secara deterministik:
  1. `read_file` -> `read_file`
  2. `list_dir` -> `list_dir`
  3. `snake_case_name` -> `snake_case_name`
  4. `app/main.py` -> `app/main.py`
  5. `__init__.py` -> `__init__.py`
  6. `_private` -> `_private`
  7. `2 * 3 * 4` -> `2 * 3 * 4`
  8. Teks dengan format markdown `**tebal**` serta `*miring*` tetap dibersihkan (`tebal` dan `miring`).
  Hasil pembersihan dilarang menghapus underscore di dalam kata.
- **F3 (Telemetri Child Process & Time-to-First-Audio):** Smoke test Tahap 4B diulang lewat skrip otomatis dan mengukur proses anak PowerShell (`powershell.exe`) via `psutil` (puncak RAM proses anak dan puncak CPU proses anak), serta memisahkan `time_to_first_audio_ms` dari `total_playback_ms`. Jika `time_to_first_audio` tidak dapat diukur secara presisi tanpa hook audio hardware, alasan teknisnya dicatat secara transparan.
- **F4 (Verifikasi Perintah /mute dan /unmute):** Memverifikasi lewat tes bahwa perintah `/mute` dan `/unmute` ada dan mengubah `config.json` (`/mute` -> `is_muted: True`, `/unmute` -> `is_muted: False`), serta fungsi `speak()` tidak memanggil engine saat status mute aktif.
- **F5 (Default is_muted True):** Nilai default `is_muted` pada `DEFAULT_CONFIG` diset ke `True` sampai suara id-ID tersedia di Tahap 4C.
- **F6 (Koreksi Dokumentasi T4 di Plan):** Koreksi T4 dicantumkan secara jujur di plan bahwa `OMP_NUM_THREADS` tidak berdampak pada SAPI5/PowerShell dan metrik CPU yang valid adalah F3.
- **F7 (Integritas Suite & test_ids.txt):** Seluruh suite pengujian lulus, tidak ada test lama yang diubah, dan `docs/test_ids.txt` cocok 100% dengan hasil discovery.

#### Hasil Evaluasi Tahap 4B-1:
Status Tahap 4B-1 resmi: **LULUS** (seluruh kriteria F1–F7 mencapai 100%).
- **F1 (Ollama Aktif & Suite Penuh):** **100% LULUS** (daemon Ollama aktif, `ollama ps` berjalan tanpa error; seluruh suite pengujian dieksekusi dengan `testsRun: 117`, `successes: 114`, `skipped: 3` [hanya 3 suite frozen benchmark Set 2, Set 3, Set 4]; `test_llm_indirect_prompt_injection_safety` berjalan penuh dan lulus 100%).
- **F2 (Preservasi Identifier & Formula):** **100% LULUS** (8 kasus uji tetap diuji pada `test_f2_clean_text_preserves_identifiers_and_paths`: `read_file`, `list_dir`, `snake_case_name`, `app/main.py`, `__init__.py`, `_private`, `2 * 3 * 4` dipertahankan 100% tanpa ada underscore yang terhapus; `**tebal**` serta `*miring*` tetap dibersihkan menjadi `tebal` dan `miring`).
- **F3 (Telemetri Child Process & Time-to-First-Audio):** **100% SUKSES** (`docs/evidence/tahap4b1_smoke.txt`: proses anak `powershell.exe` terukur via `psutil`: rata-rata TTFA terpisah sebesar 364.27 ms, total playback rata-rata 8278.58 ms / kalimat, puncak RAM proses anak 100.12 MB, puncak CPU proses anak 102.6% [~1 core], interupsi instan 54.12 ms, zero-cost mute 0.0014 ms, 0 crash).
- **F4 (Perintah /mute dan /unmute):** **100% LULUS** (diverifikasi lewat `test_f4_f5_mute_unmute_config_and_speak_behavior`: `/mute` mengeset `is_muted: True`, `/unmute` mengeset `is_muted: False`, perubahan tersimpan ke berkas konfigurasi, dan saat mute fungsi `speak()` menghasilkan 0 panggilan engine audio).
- **F5 (Default is_muted True):** **100% LULUS** (`DEFAULT_CONFIG["is_muted"]` diset `True` di `enki/config.py`; berkas `config.json` pengguna di disk tidak diubah paksa).
- **F6 (Koreksi Dokumentasi T4 di Plan):** **SELESAI** (koreksi dicantumkan secara jujur pada catatan desain v2.9 bahwa `OMP_NUM_THREADS` tidak berdampak pada SAPI5/PowerShell dan metrik CPU yang valid adalah F3).
- **F7 (Integritas Suite & test_ids.txt):** **100% LULUS** (117 test discovered, 117 recorded in `docs/test_ids.txt`, selisih 0 ID, 0 test lama diubah, 0 failure, 0 error).

#### Langkah Selanjutnya:
Tahap 4C: Integrasi Suara Bahasa Indonesia (id-ID) menunggu pengguna memasang paket suara id-ID di Windows dan hasil evaluasi `survey_tts.py`.

### Tahap 5: Screen Reader (OCR Bounding Box) (AKAN DATANG)
* Deteksi bounding box window aktif.
* Komparasi langsung Windows Media OCR vs RapidOCR pada layar VS Code.
* Integrasi tool `screen_inspect`.

### Tahap 6: Web Tools (`open_url`, `web_fetch`) & Evaluasi Menyeluruh (AKAN DATANG)
* Implementasi `web_fetch` (trafilatura + requests) dengan sanitasi tag injection.
* Integrasi `open_url` aman.
* Pengujian integrasi ujung ke ujung seluruh kapabilitas ENKI.
