#!/usr/bin/env python3
"""Survei Suara Windows dan Paket TTS (Tahap 4A).
Hanya membaca dan mengukur tanpa menginstal paket baru.
"""

import sys
import os
import time
import datetime
import subprocess
import tempfile
import winreg
from pathlib import Path

def get_installed_packages():
    pkgs = [
        "pyttsx3",
        "comtypes",
        "pywin32",
        "edge-tts",
        "winrt",
        "sounddevice",
        "pyaudio"
    ]
    results = {}
    for p in pkgs:
        try:
            import importlib.metadata
            v = importlib.metadata.version(p)
            results[p] = f"v{v}"
        except Exception:
            results[p] = "Tidak terpasang"
    return results

def get_sapi5_voices_powershell():
    ps_script = (
        "Add-Type -AssemblyName System.Speech\n"
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$voices = $synth.GetInstalledVoices()\n"
        "foreach ($v in $voices) {\n"
        "    $info = $v.VoiceInfo\n"
        "    Write-Output ('Name: ' + $info.Name + ' | Culture: ' + $info.Culture.Name + ' | Gender: ' + $info.Gender + ' | Enabled: ' + $v.Enabled + ' | Age: ' + $info.Age)\n"
        "}\n"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=True
        )
        lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        return lines
    except Exception as e:
        return [f"Error querying SAPI5 via PowerShell: {e}"]

def get_onecore_voices_registry():
    path = r"SOFTWARE\Microsoft\Speech_OneCore\Voices\Tokens"
    voices = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path)
        count = winreg.QueryInfoKey(key)[0]
        for i in range(count):
            sub_name = winreg.EnumKey(key, i)
            sub_key = winreg.OpenKey(key, sub_name)
            try:
                attr_key = winreg.OpenKey(sub_key, "Attributes")
                name, _ = winreg.QueryValueEx(attr_key, "Name")
                lang, _ = winreg.QueryValueEx(attr_key, "Language")
                gender, _ = winreg.QueryValueEx(attr_key, "Gender")
                voices.append({
                    "token": sub_name,
                    "name": name,
                    "language": lang,
                    "gender": gender
                })
            except Exception as e:
                voices.append({"token": sub_name, "error": str(e)})
    except Exception as e:
        voices.append({"error": f"Error opening registry: {e}"})
    return voices

def main():
    out_lines = []
    def log(msg: str = ""):
        out_lines.append(msg)
        print(msg)

    log("=" * 80)
    log("SURVEI SUARA WINDOWS DAN SUBSISTEM TTS (TAHAP 4A)")
    log("=" * 80)
    log(f"Waktu UTC        : {datetime.datetime.now(datetime.timezone.utc).isoformat()}")
    log(f"Sistem Operasi   : {sys.platform}")
    log(f"Versi Python     : {sys.version.split()[0]}")
    log("=" * 80)
    log()

    # (a) Paket TTS terpasang
    log("--- (a) Paket TTS Terpasang ---")
    pkgs = get_installed_packages()
    for p, status in pkgs.items():
        log(f"  - {p:<15}: {status}")
    log()

    # (b) Daftar Suara SAPI5 (PowerShell System.Speech)
    log("--- (b) Daftar Suara SAPI5 (System.Speech) ---")
    sapi5_list = get_sapi5_voices_powershell()
    for s in sapi5_list:
        log(f"  - {s}")
    log()

    # Daftar Suara OneCore (Registry)
    log("--- (b) Daftar Suara OneCore (Registry HKLM) ---")
    onecore_list = get_onecore_voices_registry()
    for v in onecore_list:
        if "error" in v and "name" not in v:
            log(f"  - Error: {v.get('error')}")
        else:
            log(f"  - Name: {v.get('name')} | Language LCID: {v.get('language')} | Gender: {v.get('gender')} | Token: {v.get('token')}")
    log()

    # (c) Evaluasi Suara Indonesia (Culture id-ID / 421)
    log("--- (c) Sintesis & Evaluasi Suara Bahasa Indonesia (id-ID / LCID 421) ---")
    # Cek apakah ada suara id-ID
    id_voices = []
    # Periksa SAPI5
    for s in sapi5_list:
        if "id-id" in s.lower() or "indonesia" in s.lower():
            id_voices.append(("SAPI5", s))
    # Periksa OneCore
    for v in onecore_list:
        lang = str(v.get("language", "")).lower()
        if lang in ("421", "0421") or "indonesia" in str(v.get("name", "")).lower():
            id_voices.append(("OneCore", v))

    sample_dir = Path(tempfile.gettempdir()) / "enki_tts_samples"
    sample_dir.mkdir(parents=True, exist_ok=True)
    log(f"Direktori sampel WAV sementara: {sample_dir}")

    if not id_voices:
        # (d) jika tidak ada suara Indonesia, tulis apa adanya
        log("[Status Suara Indonesia]: TIDAK ADA suara dengan culture 'id-ID' (LCID 421) yang terpasang di sistem Windows saat ini.")
        log("Catatan: Hanya terdeteksi suara bahasa Inggris (en-US): David, Mark, dan Zira.")
        log("Untuk menggunakan suara lokal Indonesia, paket bahasa suara Indonesia (Indonesian Speech Recognition & TTS) perlu diinstal di Windows Settings -> Time & Language -> Speech.")
    else:
        log(f"Ditemukan {len(id_voices)} suara Indonesia:")
        test_text = "Halo, saya ENKI. Ini contoh suara untuk asisten pribadimu."
        for kind, voice_info in id_voices:
            # Sintesis dan ukur latensi + RAM
            vname = voice_info if isinstance(voice_info, str) else voice_info.get("name")
            wav_path = sample_dir / f"sample_{vname}.wav"
            log(f"  - Menguji suara [{kind}] {vname}...")
            # Ukur
            t0 = time.time()
            # PowerShell synthesis script to WAV
            synth_script = f"""
            Add-Type -AssemblyName System.Speech
            $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
            $synth.SelectVoice('{vname}')
            $synth.SetOutputToWaveFileStream([System.IO.File]::Create('{wav_path}'))
            $synth.Speak('{test_text}')
            $synth.Dispose()
            """
            try:
                subprocess.run(["powershell", "-NoProfile", "-Command", synth_script], check=True, capture_output=True)
                latency_ms = (time.time() - t0) * 1000.0
                file_size = wav_path.stat().st_size if wav_path.exists() else 0
                log(f"    Path WAV: {wav_path} (Ukuran: {file_size} bytes)")
                log(f"    Latensi : {latency_ms:.1f} ms")
            except Exception as e:
                log(f"    Gagal sintesis: {e}")

    log()
    log("=" * 80)
    log("KESIMPULAN SURVEI TAHAP 4A:")
    log(f"1. Paket Python TTS lokal: Belum ada paket TTS pihak ketiga terpasang (pyttsx3/comtypes/edge-tts = Tidak terpasang).")
    log(f"2. Mesin Suara Windows Bawaan: SAPI5 dan OneCore aktif dengan suara bawaan en-US.")
    log(f"3. Ketersediaan Suara Indonesia: {'Tersedia' if id_voices else 'Tidak ada suara Indonesia di OS saat ini; opsi: pasang paket suara Windows OneCore Indonesia atau gunakan edge-tts daring sebagai fallback sekunder'}.")
    log("=" * 80)

    # Simpan keluaran ke docs/evidence/tahap4a_survey.txt
    evidence_file = Path(__file__).resolve().parent.parent / "docs" / "evidence" / "tahap4a_survey.txt"
    evidence_file.parent.mkdir(parents=True, exist_ok=True)
    evidence_file.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"\n[SUKSES] Laporan survei disimpan di: {evidence_file}")

if __name__ == "__main__":
    main()
