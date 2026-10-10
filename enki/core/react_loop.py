import time
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Callable, Generator, Tuple

from enki.core.ollama_client import OllamaClient
from enki.core.prompt_assembler import PromptAssembler, truncate_tool_output, estimate_tokens_local
from enki.core.sanitizer import wrap_untrusted_content
from enki.memory.database import MemoryDatabase
from enki.memory.recall import format_recall_block, is_recall_triggered, build_fts_query
from enki.tools.file_tools import execute_read_file
from enki.tools.shell_tools import (
    execute_git_command,
    execute_python_version,
    execute_run_unittests,
    execute_list_dir
)
from enki.tools.tool_types import ToolExecutionResult, ToolResultStatus

import re

# -------------------------------------------------------------
# Tool Schemas Tetap (Fixed Argument Tools)
# -------------------------------------------------------------
SEARCH_MEMORY_TOOL_SCHEMA: Dict[str, Any] = {
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
}

READ_FILE_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": "Membaca isi teks file dokumen atau kode program di workspace (misal .py, .json, .txt, .md, .conf). JANGAN gunakan untuk mengeksekusi script.",
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
}

GIT_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "git",
        "description": "Menjalankan perintah Git allowlist di workspace. Aksi yang didukung: 'status' (status working tree), 'log' (riwayat commit), 'diff' (perubahan kode). Memerlukan konfirmasi manual pengguna.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "log", "diff"],
                    "description": "Aksi git: 'status', 'log', atau 'diff'"
                }
            },
            "required": ["action"]
        }
    }
}

PYTHON_VERSION_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "python_version",
        "description": "Memeriksa versi Python yang terpasang di sistem (python --version). Memerlukan konfirmasi manual pengguna.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
}

RUN_UNITTESTS_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "run_unittests",
        "description": "Menjalankan unit test Python di workspace menggunakan modul standar unittest (python -m unittest). Memerlukan konfirmasi manual pengguna.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
}

LIST_DIR_TOOL_SCHEMA: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "list_dir",
        "description": "Melihat daftar file dan folder di dalam direktori workspace. JANGAN gunakan untuk mengeksekusi program.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path relatif folder dalam workspace yang ingin dilihat isinya (default '.' untuk root workspace)"
                }
            }
        }
    }
}

EXPLICIT_MEMORY_SEARCH_REGEX = re.compile(
    r'\b(catatan|riwayat|log\s+obrolan|sesi\s+lampau|sesi\s+sebelumnya|percakapan\s+lama|memori)\b',
    re.IGNORECASE
)

def should_enable_memory_tool(user_input: str) -> bool:
    """Menentukan secara deterministik apakah search_memory harus diaktifkan dalam daftar tools."""
    if not user_input:
        return False
    return is_recall_triggered(user_input) or bool(EXPLICIT_MEMORY_SEARCH_REGEX.search(user_input))

# -------------------------------------------------------------
# Pola Gating Deterministik Ketat (Word-Boundary, Case-Insensitive)
# -------------------------------------------------------------
FILE_EXTENSION_REGEX = re.compile(
    r'\b[\w\.\-/]+\.(?:py|md|txt|json|ya?ml|toml|cfg|ini|sh|csv|log)\b',
    re.IGNORECASE
)
FILE_KEYWORD_REGEX = re.compile(
    r'\b(file|berkas)\b',
    re.IGNORECASE
)
FILE_VALID_PATH_REGEX = re.compile(
    r'(?:'
    r'(?:\.{1,2}[/\\][\w\.\-/]+)'          # diawali ./ atau ../
    r'|'
    r'(?:\b[a-zA-Z]:[/\\][\w\.\-/]+)'      # diawali huruf drive
    r'|'
    r'(?:\b(?:app|tests?|docs?|enki|scripts?)[/\\][\w\.\-/]+)' # folder workspace
    r')',
    re.IGNORECASE
)

def should_enable_read_file(user_input: str) -> bool:
    if not user_input:
        return False
    return bool(
        FILE_EXTENSION_REGEX.search(user_input)
        or FILE_KEYWORD_REGEX.search(user_input)
        or FILE_VALID_PATH_REGEX.search(user_input)
    )

GIT_PRIMARY_REGEX = re.compile(
    r'\b(git|commit|branch|diff)\b',
    re.IGNORECASE
)

def should_enable_git(user_input: str) -> bool:
    if not user_input:
        return False
    # git|commit|branch|diff selalu memicu; status/log/repo hanya bila bersama "git"
    return bool(GIT_PRIMARY_REGEX.search(user_input))

UNITTESTS_EXPLICIT_REGEX = re.compile(
    r'\b(unittest|unit\s+test|pytest)\b',
    re.IGNORECASE
)
TEST_RUN_ACTION_REGEX = re.compile(
    r'\b(jalankan|jalanin|run)\b',
    re.IGNORECASE
)
TEST_NOUN_REGEX = re.compile(
    r'\b(test|tes)\b',
    re.IGNORECASE
)
TEST_SCOPE_REGEX = re.compile(
    r'\b(unit|proyek|project|kode|semua|seluruh)\b',
    re.IGNORECASE
)

def should_enable_run_unittests(user_input: str) -> bool:
    if not user_input:
        return False
    if UNITTESTS_EXPLICIT_REGEX.search(user_input):
        return True
    return bool(
        TEST_RUN_ACTION_REGEX.search(user_input)
        and TEST_NOUN_REGEX.search(user_input)
        and TEST_SCOPE_REGEX.search(user_input)
    )

PYTHON_WORD_REGEX = re.compile(r'\bpython\b', re.IGNORECASE)
VERSION_WORD_REGEX = re.compile(r'\b(versi|version)\b', re.IGNORECASE)

def should_enable_python_version(user_input: str) -> bool:
    if not user_input:
        return False
    return bool(PYTHON_WORD_REGEX.search(user_input) and VERSION_WORD_REGEX.search(user_input))

LIST_DIR_REGEX = re.compile(
    r'\b(folder|direktori|ls|dir)\b',
    re.IGNORECASE
)

def should_enable_list_dir(user_input: str) -> bool:
    if not user_input:
        return False
    return bool(LIST_DIR_REGEX.search(user_input))

def get_active_tools_for_turn(user_input: str) -> Optional[List[Dict[str, Any]]]:
    """Mengembalikan daftar schema tools tetap yang relevan untuk turn ini.
    Menerapkan gating leksikal deterministik ketat:
    - Tidak ada yang cocok -> None (tidak ada tools yang dikirim ke model).
    """
    tools = []
    if should_enable_memory_tool(user_input):
        tools.append(SEARCH_MEMORY_TOOL_SCHEMA)
    if should_enable_read_file(user_input):
        tools.append(READ_FILE_TOOL_SCHEMA)
    if should_enable_git(user_input):
        tools.append(GIT_TOOL_SCHEMA)
    if should_enable_python_version(user_input):
        tools.append(PYTHON_VERSION_TOOL_SCHEMA)
    if should_enable_run_unittests(user_input):
        tools.append(RUN_UNITTESTS_TOOL_SCHEMA)
    if should_enable_list_dir(user_input):
        tools.append(LIST_DIR_TOOL_SCHEMA)

    return tools if tools else None

class ReActCoordinator:
    def __init__(
        self,
        client: OllamaClient,
        assembler: PromptAssembler,
        workspace_root: Path,
        db: MemoryDatabase,
        confirm_func: Optional[Callable[[str], bool]] = None,
        audit_log_path: Optional[Path] = None,
        max_iterations: int = 5,
        turn_timeout_sec: int = 120
    ):
        self.client = client
        self.assembler = assembler
        self.workspace_root = Path(workspace_root)
        self.db = db
        self.confirm_func = confirm_func
        self.audit_log_path = audit_log_path
        self.max_iterations = max_iterations
        self.turn_timeout_sec = turn_timeout_sec

    def execute_tool(self, tool_name: str, args: Dict[str, Any], session_id: str) -> ToolExecutionResult:
        """Mengeksekusi tool dengan validasi argumen ketat.
        Returns: ToolExecutionResult
        """
        if tool_name == "search_memory":
            query = args.get("query")
            if not query or not isinstance(query, str):
                return ToolExecutionResult(ToolResultStatus.INVALID_ARG, "[Validasi Argumen Gagal] Argumen 'query' (string) wajib disediakan untuk search_memory.")
            fts_query = build_fts_query(query.strip()) or query.strip()
            snippets = self.db.search_fts(fts_query, exclude_session_id=session_id, limit=3)
            if not snippets:
                return ToolExecutionResult(ToolResultStatus.OK, "[Tool Result: search_memory] Tidak ditemukan catatan yang cocok di database memori.")
            res = format_recall_block(snippets, max_tokens=1000)
            return ToolExecutionResult(ToolResultStatus.OK, res or "[Tool Result: search_memory] Hasil kosong.")

        elif tool_name == "read_file":
            file_path = args.get("file_path")
            if not file_path or not isinstance(file_path, str):
                return ToolExecutionResult(ToolResultStatus.INVALID_ARG, "[Validasi Argumen Gagal] Argumen 'file_path' (string) wajib disediakan untuk read_file.")
            return execute_read_file(file_path.strip(), self.workspace_root)

        elif tool_name == "git":
            action = args.get("action")
            if not action or not isinstance(action, str):
                return ToolExecutionResult(ToolResultStatus.INVALID_ARG, "[Validasi Argumen Gagal] Argumen 'action' (string enum: 'status', 'log', 'diff') wajib disediakan untuk git.")
            return execute_git_command(
                action=action.strip(),
                workspace_root=self.workspace_root,
                confirm_func=self.confirm_func,
                audit_log_path=self.audit_log_path,
                timeout_sec=30
            )

        elif tool_name == "python_version":
            return execute_python_version(
                workspace_root=self.workspace_root,
                confirm_func=self.confirm_func,
                audit_log_path=self.audit_log_path,
                timeout_sec=15
            )

        elif tool_name == "run_unittests":
            return execute_run_unittests(
                workspace_root=self.workspace_root,
                confirm_func=self.confirm_func,
                audit_log_path=self.audit_log_path,
                timeout_sec=60
            )

        elif tool_name == "list_dir":
            path = args.get("path", ".")
            if path is not None and not isinstance(path, str):
                return ToolExecutionResult(ToolResultStatus.INVALID_ARG, "[Validasi Argumen Gagal] Argumen 'path' harus berupa string.")
            return execute_list_dir(
                rel_path=path,
                workspace_root=self.workspace_root,
                audit_log_path=self.audit_log_path
            )

        return ToolExecutionResult(ToolResultStatus.POLICY_REJECTION, f"[Kebijakan Ditolak] Tool '{tool_name}' tidak terdaftar.")

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
        Menerapkan kebijakan:
        - Penolakan kebijakan (sandbox, traversal, unauthorized) -> request berikutnya tanpa tools.
        - Argumen tidak valid -> diperbolehkan retry maksimal 1 kali.
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
        had_policy_rejection = False
        arg_retry_count = 0

        for iteration in range(1, self.max_iterations + 1):
            if time.time() - start_time > self.turn_timeout_sec:
                if trace_callback:
                    trace_callback("timeout", f"ReAct loop melebihi batas waktu ({self.turn_timeout_sec}s). Menghentikan iterasi.")
                break

            # 1. Tentukan tools untuk iterasi ini
            if model == "qwen2.5-coder:3b" or had_policy_rejection:
                # Mode coder atau setelah penolakan kebijakan: WAJIB tanpa tools agar model menghasilkan teks
                turn_tools = None
            else:
                turn_tools = get_active_tools_for_turn(user_input)

            chat_res = self.client.chat(
                model=model,
                messages=current_messages,
                num_ctx=num_ctx,
                temperature=temperature,
                tools=turn_tools
            )

            tool_calls = chat_res.get("tool_calls")

            # Jika TIDAK ada tool call, model siap memberikan jawaban akhir
            if not tool_calls:
                assistant_text = chat_res.get("content", "")
                yield assistant_text, chat_res.get("metrics")
                return

            asst_msg = {
                "role": "assistant",
                "content": chat_res.get("content") or "",
                "tool_calls": tool_calls
            }
            current_messages.append(asst_msg)

            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                raw_args = fn.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args)
                    except Exception:
                        args = {}
                else:
                    args = raw_args or {}

                # Pengaman Loop: Tool identik berturut-turut
                call_sig = (name, json.dumps(args, sort_keys=True))
                if call_sig == last_call_signature:
                    guard_msg = f"[Loop Guard] Tool '{name}' dengan argumen yang sama dipanggil 2 kali berturut-turut. Dibatalkan."
                    if trace_callback:
                        trace_callback("tool_call", f"{name}({json.dumps(args)})")
                        trace_callback("tool_result", guard_msg)
                    current_messages.append({"role": "tool", "content": guard_msg})
                    had_policy_rejection = True
                    break

                last_call_signature = call_sig

                if trace_callback:
                    trace_callback("tool_call", f"{name}({json.dumps(args)})")

                # Eksekusi tool
                tool_res = self.execute_tool(name, args, session_id=session_id)

                if tool_res.is_policy_rejection:
                    had_policy_rejection = True
                elif not tool_res.is_success:
                    # Validasi argumen gagal: maksimal 1 kali retry
                    arg_retry_count += 1
                    if arg_retry_count > 1:
                        had_policy_rejection = True

                tool_output = tool_res.output

                if trace_callback:
                    output_preview = tool_output[:120].replace("\n", " ")
                    trace_callback("tool_result", output_preview)

                current_messages.append({
                    "role": "tool",
                    "content": tool_output
                })

            current_messages = self.compress_old_observations(current_messages, keep_latest=1)

        # Jika mencapai batas iterasi tanpa balasan, panggil streaming penutup tanpa tools
        if trace_callback:
            trace_callback("max_iterations", f"Mencapai batas maksimal {self.max_iterations} iterasi. Menghasilkan jawaban penutup.")

        for token, metrics in self.client.chat_stream(
            model=model,
            messages=current_messages,
            num_ctx=num_ctx,
            temperature=temperature
        ):
            yield token, metrics
