# ENKI

Asisten AI pribadi yang **sepenuhnya lokal**, dirancang untuk laptop dengan **NVIDIA RTX 3050 4 GB VRAM**.

ENKI dibangun di sekeliling keterbatasan nyata model kecil (3B), bukan dengan berpura-pura keterbatasan itu tidak ada. Hal-hal yang rawan salah diserahkan ke kode Python yang deterministik, bukan ke model: pemilihan alat, keamanan, memori, dan akses internet.

> **Status:** proyek ditutup/diarsipkan pada Oktober 2026 setelah Tahap 1-7 selesai. Lihat bagian [Status dan keterbatasan](#status-dan-keterbatasan) untuk yang jujur soal apa yang belum beres.

## Fitur



- **Chat lokal** dengan `qwen2.5:3b` lewat [Ollama](https://ollama.com) (context 8192, suhu 0.2). Tidak ada data yang keluar dari komputer, kecuali perintah web yang kamu konfirmasi sendiri.
- **Memori jangka panjang**
  - Riwayat sesi di SQLite (FTS5) dengan pemanggilan ulang aktif lewat kata pemicu ("kemarin", "pernah", "ingat", ...).
  - Profil pengguna (`profile.json`) yang hanya diubah lewat antrean usulan: refleksi otomatis mengusulkan fakta, kamu yang menyetujui (`/memory approve`).
  - Filter deterministik: kutipan sumber harus muncul verbatim di pesan pengguna, kata pengandaian ditolak, pola kredensial dibuang.
- **Alat berargumen tetap** (tanpa shell bebas): `read_file`, `list_dir`, `git` (status/log/diff), `python_version`, `run_unittests`, `search_memory`. Path diisolasi ke workspace, dan setiap aksi sistem butuh konfirmasi `[y/N]`.
- **Router kemampuan** (tanpa LLM): pertanyaan seperti "kamu bisa apa?" atau "bisa buka internet?" dijawab dari registri perintah oleh kode, jadi tidak bisa berhalusinasi soal kemampuan sendiri.
- **Suara (TTS) offline**: Supertonic 3 (ONNX, suara Indonesia) dengan fallback SAPI5. Mute tanpa biaya (0 sintesis, 0 thread, 0 library audio saat senyap), teks di layar disinkronkan dengan suara.
- **Alat web yang aman** (semua butuh konfirmasi `[y/N]` yang menampilkan URL persisnya):
  - `/web <url> [pertanyaan]`: ambil teks halaman lalu ringkas/jawab.
  - `/wiki <topik>`: ringkasan Wikipedia ditampilkan apa adanya, **tanpa LLM**, supaya bebas halusinasi.
  - `/buka <url>`: buka URL di browser sistem.
- **Skill**: folder `skills/<nama>/SKILL.md` berisi instruksi teks (gaya jawab, format) yang bisa diaktifkan dengan `/skill <nama>`. Skill hanya instruksi untuk model; ia tidak bisa menjalankan apa pun atau menambah kemampuan.

## Keamanan

- Model **tidak pernah** memegang alat web atau internet di percakapan biasa; akses hanya lewat perintah eksplisit yang kamu konfirmasi.
- Pertahanan SSRF untuk `/web` dan `/buka`: hanya http/https, port 80/443, blokir localhost/IP privat/link-local/IPv6 dan variasi IP (desimal/heks), blokir userinfo, semua IP hasil DNS harus publik, redirect maksimal 3 kali dan divalidasi ulang.
- Batas teknis: timeout, batas unduhan 1,5 MB, MIME allowlist, ekstraksi teks dengan `trafilatura`.
- Konten web dibungkus sebagai `<untrusted_web_content>` dan disanitasi; hanya pertanyaan dan jawaban yang disimpan ke riwayat. Baris "Sumber:" dicetak oleh kode, bukan oleh model.
- Audit log semua eksekusi alat di `logs/tool_audit.log`.

## Kebutuhan

- Windows 10/11 (suara memakai `winsound`/SAPI5; bagian lain umumnya portabel)
- Python 3 dan `pip`
- [Ollama](https://ollama.com) terpasang dan berjalan
- GPU NVIDIA 4 GB VRAM disarankan (model `qwen2.5:3b` memakai sekitar 2,6 GB)

## Instalasi

```powershell
git clone <url-repo-ini>
cd Enki

python -m venv venv
.\venv\Scripts\python -m pip install -r requirements.txt

ollama pull qwen2.5:3b
```

Kebijakan Ollama yang dipakai proyek ini (opsional tapi disarankan):

```powershell
[System.Environment]::SetEnvironmentVariable('OLLAMA_MAX_LOADED_MODELS', '1', 'User')
```

## Menjalankan

```powershell
.\venv\Scripts\python main.py
```

Ketik pesan untuk mengobrol, atau `/help` untuk daftar perintah. Beberapa perintah:

| Perintah | Fungsi |
|---|---|
| `/help` | Daftar perintah |
| `/mute`, `/unmute` | Matikan/nyalakan suara |
| `/memory view\|pending\|approve\|reject\|edit\|delete\|log\|stats` | Kelola profil dan antrean usulan fakta |
| `/reflect` | Jalankan refleksi atas sesi lampau |
| `/web <url> [tanya]` | Baca halaman web (dengan konfirmasi) |
| `/wiki <topik>` | Ringkasan Wikipedia (dengan konfirmasi) |
| `/buka <url>` | Buka URL di browser (dengan konfirmasi) |
| `/skill list\|<nama>\|off` | Atur skill aktif |
| `/exit` | Keluar |

## Struktur proyek

```
enki/
  core/      prompt assembler, router kemampuan, ReAct loop, skill, memori
  cli/       konsol interaktif dan registri perintah
  audio/     TTS (Supertonic, SAPI5)
  tools/     alat berargumen tetap dan alat web
skills/      contoh skill (gaya-ringkas, format-ringkasan)
tests/       suite unit test
scripts/     skrip benchmark dan evaluasi
docs/        implementation_plan.md dan bukti (evidence/)
```

## Pengujian

```powershell
.\venv\Scripts\python -m unittest discover -s tests
```

Suite berisi 243 tes (3 dilewati). Sebagian tes memakai model nyata sehingga **Ollama harus berjalan**; tes berbasis model bersifat stokastik dan sesekali bisa gagal pada satu run.

## Pendekatan rekayasa

Proyek ini dikerjakan dengan disiplin evaluasi yang ketat:

- Kriteria kelulusan ditulis dan di-commit **sebelum** kode.
- Ambang (gate) ditetapkan di muka dan **tidak disetel ulang** setelah hasil gagal.
- Bukti dihasilkan script dan disimpan di `docs/evidence/` dengan Git HEAD dan status tree.
- Set uji tertahan (*held-out*) ditulis terpisah dan tidak dilihat saat mengembangkan.
- Semua temuan, termasuk yang gagal, dicatat apa adanya di `docs/implementation_plan.md`.

## Status dan keterbatasan

- **Tahap 1-2, 4, 6, 7** (shell CLI, memori, suara, web tools, skill): selesai dan diterima.
- **Tahap 3** (ReAct loop dan alat): **belum lulus gate**. Benchmark gating ReAct gagal pada beberapa kriteria (Set 4A: kriteria 1, 6, 7), dan tidak disetel ulang setelah gagal.
- **Router kemampuan**: gagal uji tertahan (10/16 pertanyaan positif lolos, 1 salah arah); recall pada kalimat yang belum pernah dilihat sekitar 62%. Dipakai sebagai upaya terbaik (*best-effort*).
- **Recall pasif** otomatis dinonaktifkan karena gagal gate (terlalu banyak usulan salah).
- **Model 3B** bisa berhalusinasi pada fakta umum dan mengklaim telah melakukan aksi yang sebenarnya tidak dilakukan. Karena itu fakta Wikipedia ditampilkan langsung tanpa model.
- **`/buka`**: di sebagian pengaturan Windows/Edge, perintah terkirim dan tab terbuat tetapi jendela browser tidak muncul di depan. Kode ENKI terbukti benar lewat pengujian otomatis, jadi ini keterbatasan lingkungan; URL selalu dicetak agar bisa dibuka manual.
- Input suara (STT) dan avatar 3D dibuang dari lingkup proyek.

## Lisensi

Belum ditentukan. Tambahkan berkas `LICENSE` sebelum membagikan proyek ini secara luas.
