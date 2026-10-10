import sqlite3
import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from enki.config import get_log_dir

def get_default_db_path() -> Path:
    return get_log_dir() / "sessions.db"

class MemoryDatabase:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else get_default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        with self.conn:
            # 1. Tabel sessions
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    started_at TEXT NOT NULL,
                    reflected_at TEXT
                )
            """)
            # 2. Tabel turns
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS turns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            # 3. Virtual table FTS5 untuk pencarian full-text (Hanya role user & assistant)
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS turns_fts USING fts5(
                    turn_id UNINDEXED,
                    session_id UNINDEXED,
                    role UNINDEXED,
                    content
                )
            """)

    def create_session(self, session_id: Optional[str] = None) -> str:
        """Membuat sesi obrolan baru."""
        if not session_id:
            now_str = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            session_id = f"session_{now_str}"

        started_at = datetime.datetime.now().isoformat()
        with self.conn:
            self.conn.execute(
                "INSERT INTO sessions (id, started_at, reflected_at) VALUES (?, ?, NULL)",
                (session_id, started_at)
            )
        return session_id

    def add_turn(self, session_id: str, role: str, content: str) -> int:
        """Menyimpan turn ke DB dan indeks FTS5, langsung commit saat itu juga."""
        ts = datetime.datetime.now().isoformat()
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO turns (session_id, role, content, ts) VALUES (?, ?, ?, ?)",
                (session_id, role, content, ts)
            )
            turn_id = cursor.lastrowid

            # FTS5 hanya mengindeks teks user dan assistant, BUKAN output tool atau sistem
            if role in ("user", "assistant"):
                self.conn.execute(
                    "INSERT INTO turns_fts (turn_id, session_id, role, content) VALUES (?, ?, ?, ?)",
                    (turn_id, session_id, role, content)
                )
        return turn_id

    def get_unreflected_sessions(self) -> List[str]:
        """Mengambil daftar session_id yang belum pernah direfleksi."""
        cursor = self.conn.execute(
            "SELECT id FROM sessions WHERE reflected_at IS NULL ORDER BY started_at ASC"
        )
        return [row["id"] for row in cursor.fetchall()]

    def mark_session_reflected(self, session_id: str):
        """Menandai sesi telah selesai direfleksi."""
        now_iso = datetime.datetime.now().isoformat()
        with self.conn:
            self.conn.execute(
                "UPDATE sessions SET reflected_at = ? WHERE id = ?",
                (now_iso, session_id)
            )

    def get_user_turns(self, session_id: str) -> List[str]:
        """Mengambil semua pesan bertipe 'user' dari sebuah sesi untuk refleksi."""
        cursor = self.conn.execute(
            "SELECT content FROM turns WHERE session_id = ? AND role = 'user' ORDER BY id ASC",
            (session_id,)
        )
        return [row["content"] for row in cursor.fetchall()]

    def search_fts(self, query: str, exclude_session_id: str, limit: int = 3) -> List[Dict[str, Any]]:
        """Mencari rekaman FTS5 dengan ranking BM25, mengecualikan sesi aktif."""
        sql = """
            SELECT turn_id, session_id, role, content, rank
            FROM turns_fts
            WHERE turns_fts MATCH ? AND session_id != ?
            ORDER BY rank
            LIMIT ?
        """
        try:
            cursor = self.conn.execute(sql, (query, exclude_session_id, limit))
            return [dict(row) for row in cursor.fetchall()]
        except sqlite3.OperationalError:
            # Jika query syntax invalid di FTS5, kembalikan list kosong
            return []

    def get_term_turn_frequency(self, term: str) -> float:
        """Menghitung persentase Document Frequency (DF %) sebuah term dalam tabel turns_fts."""
        try:
            cur_total = self.conn.execute("SELECT count(*) as c FROM turns WHERE role in ('user', 'assistant')")
            total = cur_total.fetchone()["c"]
            if total == 0:
                return 0.0
            cur_match = self.conn.execute("SELECT count(*) as c FROM turns_fts WHERE turns_fts MATCH ?", (f'"{term}"',))
            matches = cur_match.fetchone()["c"]
            return (matches / total) * 100.0
        except Exception:
            return 0.0

    def close(self):
        self.conn.close()
