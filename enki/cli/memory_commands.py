from typing import List
from enki.memory.profile_manager import ProfileManager, MAX_PROFILE_TOKENS
from enki.memory.reflector import ReflectorDaemon
from enki.memory.database import MemoryDatabase
from enki.core.ollama_client import OllamaClient

def handle_memory_command(
    args: List[str],
    profile_mgr: ProfileManager,
    reflector: ReflectorDaemon,
    db: MemoryDatabase,
    client: OllamaClient,
    model: str
) -> None:
    if not args or args[0].lower() in ["help", "-h", "--help"]:
        print("\n--- Panduan Perintah /memory ---")
        print("  /memory view              : Lihat seluruh fakta profil aktif")
        print("  /memory pending           : Lihat antrean usulan fakta baru")
        print("  /memory approve <id>      : Setujui usulan fakta dan simpan ke profil")
        print("  /memory reject <id>       : Tolak usulan fakta")
        print("  /memory edit <id> <val>   : Koreksi nilai usulan fakta sebelum disetujui")
        print("  /memory delete <key>      : Hapus fakta dari profil")
        print("  /memory log               : Lihat riwayat audit perubahan profil")
        print("  /memory stats             : Lihat metrik usulan (diterima, ditolak, diedit, dibuang filter)")
        print("  /reflect                  : Jalankan refleksi manual atas sesi yang belum direfleksi\n")
        return

    subcmd = args[0].lower()

    if subcmd == "view":
        facts = profile_mgr.get_all_facts()
        tokens = profile_mgr.count_tokens()
        print(f"\n--- Profil Pengguna ({tokens}/{MAX_PROFILE_TOKENS} token) ---")
        if not facts:
            print("  (Belum ada fakta profil yang tersimpan)")
        else:
            for k, v in facts.items():
                print(f"  • {k}: {v}")
        print()

    elif subcmd == "pending":
        pending = reflector.get_pending_proposals()
        print(f"\n--- Antrean Usulan Fakta ({len(pending)} pending) ---")
        if not pending:
            print("  (Tidak ada usulan fakta yang menunggu persetujuan)")
        else:
            for p in pending:
                print(f"  [{p['id']}] Kunci: '{p['key']}' | Nilai: '{p['value']}'")
                print(f"      Kutipan: \"{p['source_quote']}\" (Sesi: {p['session_id']})")
        print()

    elif subcmd == "approve":
        if len(args) < 2:
            print("Format salah: gunakan /memory approve <id>")
            return
        try:
            pid = int(args[1])
        except ValueError:
            print("ID harus berupa angka.")
            return

        updated = reflector.update_proposal_status(pid, "APPROVED")
        if not updated:
            print(f"Proposal #{pid} tidak ditemukan.")
            return

        ok, msg = profile_mgr.add_fact(updated["key"], updated["value"])
        if ok:
            print(f"[Sukses] Proposal #{pid} disetujui: {msg}")
        else:
            # Kembalikan status jika gagal tersimpan
            reflector.update_proposal_status(pid, "PENDING")
            print(f"[Gagal] {msg}")

    elif subcmd == "reject":
        if len(args) < 2:
            print("Format salah: gunakan /memory reject <id>")
            return
        try:
            pid = int(args[1])
        except ValueError:
            print("ID harus berupa angka.")
            return

        updated = reflector.update_proposal_status(pid, "REJECTED")
        if updated:
            print(f"[Sukses] Proposal #{pid} ditolak.")
        else:
            print(f"Proposal #{pid} tidak ditemukan.")

    elif subcmd == "edit":
        if len(args) < 3:
            print("Format salah: gunakan /memory edit <id> <nilai_baru>")
            return
        try:
            pid = int(args[1])
        except ValueError:
            print("ID harus berupa angka.")
            return

        new_val = " ".join(args[2:]).strip()
        updated = reflector.update_proposal_status(pid, "PENDING", new_value=new_val)
        if updated:
            print(f"[Sukses] Nilai proposal #{pid} diubah menjadi: '{new_val}'")
        else:
            print(f"Proposal #{pid} tidak ditemukan.")

    elif subcmd == "delete":
        if len(args) < 2:
            print("Format salah: gunakan /memory delete <key>")
            return
        key = args[1].strip()
        ok, msg = profile_mgr.delete_fact(key)
        print(f"[{'Sukses' if ok else 'Gagal'}] {msg}")

    elif subcmd == "stats":
        stats = reflector.get_stats()
        print("\n--- Metrik Usulan Reflector (/memory stats) ---")
        print(f"  • Usulan Pending Aktif   : {stats.get('pending', 0)}")
        print(f"  • Disetujui (Approved)   : {stats.get('approved', 0)}")
        print(f"  • Ditolak User (Rejected): {stats.get('rejected', 0)}")
        print(f"  • Diedit User (Edited)   : {stats.get('edited', 0)}")
        print(f"  • Dibuang Filter Kode    : {stats.get('discarded_total', 0)}")
        print(f"      - Filter Rahasia     : {stats.get('discarded_secret', 0)}")
        print(f"      - Filter Hipotesis   : {stats.get('discarded_hypothesis', 0)}")
        print(f"      - Kutipan Mismatch   : {stats.get('discarded_invalid_quote', 0)}")
        print(f"  • Quote Recovery Slicing : {stats.get('quote_recovery_count', 0)} kali\n")

    elif subcmd == "log":
        logs = profile_mgr.get_audit_log(limit=15)
        print("\n--- Riwayat Audit Profil (15 Terakhir) ---")
        if not logs:
            print("  (Belum ada riwayat perubahan)")
        else:
            for l in logs:
                print(f"  {l}")
        print()

    else:
        print(f"Sub-perintah /memory tidak dikenal: '{subcmd}'. Ketik /memory untuk bantuan.")

def handle_reflect_command(
    db: MemoryDatabase,
    reflector: ReflectorDaemon,
    client: OllamaClient,
    model: str
) -> None:
    """Menjalankan refleksi manual atas seluruh sesi yang belum direfleksi."""
    unreflected = db.get_unreflected_sessions()
    print(f"\n[Reflector] Memeriksa sesi... ditemukan {len(unreflected)} sesi yang belum direfleksi.")
    if not unreflected:
        return

    total_proposals = 0
    for sid in unreflected:
        props = reflector.reflect_session(sid, db, client, model=model)
        total_proposals += len(props)

    print(f"[Reflector] Selesai. Total {total_proposals} usulan fakta baru masuk antrean (/memory pending).\n")
