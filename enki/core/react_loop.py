import time
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Generator, Tuple

from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, truncate_tool_output, estimate_tokens_local
from enki.core.sanitizer import wrap_untrusted_content
from enki.memory.database import MemoryDatabase
from enki.memory.recall import format_recall_block
from enki.tools.file_tools import execute_read_file
from enki.tools.shell_tools import execute_shell_command

ACTIVE_TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Cari catatan atau riwayat percakapan masa lalu di database memori (FTS5). Gunakan HANYA jika mencari informasi dari sesi obrolan sebelumnya.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Kata kunci pencarian substantif"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Gunakan HANYA untuk membaca/melihat isi teks file dokumen atau kode program di workspace (misal .py, .json, .txt, .md). JANGAN gunakan untuk mengeksekusi script.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path relatif file di dalam workspace yang akan dibaca"
                    }
                },
                "required": ["file_path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_shell_command",
            "description": "Gunakan HANYA untuk mengeksekusi perintah terminal allowlist di workspace (git status/log/diff, python --version, dir, python -m unittest). Memerlukan konfirmasi manual pengguna.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Perintah terminal persis yang akan dijalankan"
                    }
                },
                "required": ["command"]
            }
        }
    }
]

class ReActCoordinator:
    def __init__(
        self,
        client: OllamaClient,
        assembler: PromptAssembler,
        workspace_root: Path,
        db: MemoryDatabase,
        confirm_func: Optional[Callable[[str], bool]] = None,
        max_iterations: int = 5,
        turn_timeout_sec: int = 120
    ):
        self.client = client
        self.assembler = assembler
        self.workspace_root = Path(workspace_root)
        self.db = db
        self.confirm_func = confirm_func
        self.max_iterations = max_iterations
        self.turn_timeout_sec = turn_timeout_sec

    def execute_tool(self, tool_name: str, args: Dict[str, Any], session_id: str) -> str:
        """Mengeksekusi tool dengan validasi argumen ketat."""
        if tool_name == "search_memory":
            query = args.get("query")
            if not query or not isinstance(query, str):
                return "[Validasi Argumen Gagal] Argumen 'query' (string) wajib disediakan untuk search_memory."
            snippets = self.db.search_fts(query.strip(), exclude_session_id=session_id, limit=3)
            if not snippets:
                return "[Tool Result: search_memory] Tidak ditemukan catatan yang cocok di database memori."
            res = format_recall_block(snippets, max_tokens=1000)
            return res or "[Tool Result: search_memory] Hasil kosong."

        elif tool_name == "read_file":
            file_path = args.get("file_path")
            if not file_path or not isinstance(file_path, str):
                return "[Validasi Argumen Gagal] Argumen 'file_path' (string) wajib disediakan untuk read_file."
            return execute_read_file(file_path.strip(), self.workspace_root)

        elif tool_name == "run_shell_command":
            cmd = args.get("command")
            if not cmd or not isinstance(cmd, str):
                return "[Validasi Argumen Gagal] Argumen 'command' (string) wajib disediakan untuk run_shell_command."
            return execute_shell_command(
                cmd.strip(),
                workspace_root=self.workspace_root,
                confirm_func=self.confirm_func,
                timeout_sec=30
            )

        return f"[Tool Error] Tool '{tool_name}' tidak terdaftar."

    def compress_old_observations(self, messages: List[Dict[str, str]], keep_latest: int = 1) -> List[Dict[str, str]]:
        """Memadatkan observasi tool lama menjadi ringkasan satu baris jika anggaran context menipis."""
        tool_indices = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        if len(tool_indices) <= keep_latest:
            return messages

        prune_indices = tool_indices[:-keep_latest]
        compressed = []
        for i, m in enumerate(messages):
            if i in prune_indices:
                orig_content = m.get("content", "")
                preview = orig_content[:80].replace("\n", " ")
                compressed.append({
                    "role": "tool",
                    "content": f"[Observasi Langkah Terdahulu]: Selesai ({len(orig_content)} karakter, diringkas: {preview}...)"
                })
            else:
                compressed.append(m)
        return compressed

    def run_react_turn(
        self,
        user_input: str,
        session_id: str,
        history: List[Dict[str, str]],
        model: str = "qwen2.5:3b",
        recall_content: Optional[str] = None,
        num_ctx: int = 8192,
        temperature: float = 0.2,
        trace_callback: Optional[Callable[[str, str], None]] = None
    ) -> Generator[Tuple[str, Optional[Dict[str, Any]]], None, None]:
        """Menjalankan siklus ReAct multi-step hingga balasan final streaming.
        Yields (token_chunk, final_metrics).
        """
        start_time = time.time()

        # Rakit pesan awal
        current_messages, _ = self.assembler.assemble_messages(
            user_input=user_input,
            history=history,
            recall_content=recall_content,
            num_ctx=num_ctx
        )

        last_call_signature = None

        for iteration in range(1, self.max_iterations + 1):
            # Periksa batas waktu turn
            if time.time() - start_time > self.turn_timeout_sec:
                if trace_callback:
                    trace_callback("timeout", f"ReAct loop melebihi batas waktu ({self.turn_timeout_sec}s). Menghentikan iterasi.")
                break

            # 1. Non-streaming call untuk pemilihan aksi
            chat_res = self.client.chat(
                model=model,
                messages=current_messages,
                num_ctx=num_ctx,
                temperature=temperature,
                tools=ACTIVE_TOOL_SCHEMAS
            )

            tool_calls = chat_res.get("tool_calls")

            # Jika TIDAK ada tool call, model siap memberikan jawaban akhir
            if not tool_calls:
                assistant_text = chat_res.get("content", "")
                yield assistant_text, chat_res.get("metrics")
                return

            # Ada tool call: proses satu per satu
            # Catat pesan asisten ke transkrip loop bersama tool_calls
            asst_msg = {
                "role": "assistant",
                "content": chat_res.get("content") or "",
                "tool_calls": tool_calls
            }
            current_messages.append(asst_msg)

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments", {})

                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {"raw": raw_args}
                else:
                    args = raw_args or {}

                # 2. Pengaman Loop: Tool identik berturut-turut
                call_sig = (name, json.dumps(args, sort_keys=True))
                if call_sig == last_call_signature:
                    guard_msg = f"[Loop Guard] Tool '{name}' dengan argumen yang sama dipanggil 2 kali berturut-turut. Dibatalkan."
                    if trace_callback:
                        trace_callback("tool_call", f"{name}({json.dumps(args)})")
                        trace_callback("tool_result", guard_msg)
                    current_messages.append({"role": "tool", "content": guard_msg})
                    break

                last_call_signature = call_sig

                if trace_callback:
                    trace_callback("tool_call", f"{name}({json.dumps(args)})")

                # Eksekusi tool
                tool_output = self.execute_tool(name, args, session_id=session_id)

                if trace_callback:
                    output_preview = tool_output[:120].replace("\n", " ")
                    trace_callback("tool_result", output_preview)

                # Masukkan hasil tool ke messages
                current_messages.append({
                    "role": "tool",
                    "content": tool_output
                })

            # Kompres observasi lama jika akumulasi token observasi mulai tinggi
            current_messages = self.compress_old_observations(current_messages, keep_latest=1)

        # Jika mencapai batas iterasi tanpa jawaban final, panggil streaming final tanpa tools
        if trace_callback:
            trace_callback("max_iterations", f"Mencapai batas maksimal {self.max_iterations} iterasi. Menghasilkan jawaban penutup.")

        for token, metrics in self.client.chat_stream(
            model=model,
            messages=current_messages,
            num_ctx=num_ctx,
            temperature=temperature
        ):
            yield token, metrics
