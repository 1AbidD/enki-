import json
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional, Generator, Tuple

class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434"):
        self.base_url = base_url.rstrip("/")

    def is_ready(self) -> bool:
        """Memeriksa apakah server Ollama aktif dan merespon."""
        try:
            req = urllib.request.Request(f"{self.base_url}/api/tags")
            with urllib.request.urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    def chat(
        self,
        model: str,
        messages: List[Dict[str, str]],
        num_ctx: int = 8192,
        temperature: float = 0.2,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """Mengirim request chat non-streaming ke Ollama."""
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature
            }
        }
        if tools:
            payload["tools"] = tools

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Ollama HTTP {e.code}: {err_msg}")
        except Exception as e:
            raise RuntimeError(f"Gagal menghubungi Ollama di {self.base_url}: {e}")

        msg = data.get("message", {})
        prompt_eval_count = data.get("prompt_eval_count", 0)
        prompt_eval_duration_sec = data.get("prompt_eval_duration", 0) / 1e9
        eval_count = data.get("eval_count", 0)
        eval_duration_sec = data.get("eval_duration", 0) / 1e9

        prompt_tok_per_sec = (prompt_eval_count / prompt_eval_duration_sec) if prompt_eval_duration_sec > 0 else 0.0
        eval_tok_per_sec = (eval_count / eval_duration_sec) if eval_duration_sec > 0 else 0.0

        return {
            "content": msg.get("content", ""),
            "role": msg.get("role", "assistant"),
            "tool_calls": msg.get("tool_calls"),
            "metrics": {
                "prompt_eval_count": prompt_eval_count,
                "prompt_eval_duration_sec": round(prompt_eval_duration_sec, 3),
                "prompt_tok_per_sec": round(prompt_tok_per_sec, 1),
                "eval_count": eval_count,
                "eval_duration_sec": round(eval_duration_sec, 3),
                "eval_tok_per_sec": round(eval_tok_per_sec, 1),
                "total_duration_sec": round(data.get("total_duration", 0) / 1e9, 3)
            }
        }

    def chat_stream(
        self,
        model: str,
        messages: List[Dict[str, str]],
        num_ctx: int = 8192,
        temperature: float = 0.2,
        tools: Optional[List[Dict[str, Any]]] = None
    ) -> Generator[Tuple[str, Optional[Dict[str, Any]]], None, None]:
        """Streaming chat completion dari Ollama.
        Yields (token_text, final_metrics_dict). Pada token terakhir final_metrics_dict terisi.
        """
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_ctx": num_ctx,
                "temperature": temperature
            }
        }
        if tools:
            payload["tools"] = tools

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )

        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                tool_calls = []
                for line in resp:
                    line = line.strip()
                    if not line:
                        continue
                    chunk = json.loads(line.decode("utf-8"))
                    msg = chunk.get("message", {})
                    token = msg.get("content", "")
                    if msg.get("tool_calls"):
                        tool_calls.extend(msg.get("tool_calls"))

                    if not chunk.get("done", False):
                        if token:
                            yield token, None
                    else:
                        prompt_eval = chunk.get("prompt_eval_count", 0)
                        prompt_eval_dur = chunk.get("prompt_eval_duration", 0) / 1e9
                        eval_c = chunk.get("eval_count", 0)
                        eval_dur = chunk.get("eval_duration", 0) / 1e9
                        metrics = {
                            "prompt_eval_count": prompt_eval,
                            "prompt_eval_duration_sec": round(prompt_eval_dur, 3),
                            "prompt_tok_per_sec": round(prompt_eval / prompt_eval_dur, 1) if prompt_eval_dur > 0 else 0.0,
                            "eval_count": eval_c,
                            "eval_duration_sec": round(eval_dur, 3),
                            "eval_tok_per_sec": round(eval_c / eval_dur, 1) if eval_dur > 0 else 0.0,
                            "total_duration_sec": round(chunk.get("total_duration", 0) / 1e9, 3),
                            "tool_calls": tool_calls or None
                        }
                        yield token, metrics
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"Ollama HTTP {e.code}: {err_msg}")
        except Exception as e:
            raise RuntimeError(f"Gagal streaming dari Ollama di {self.base_url}: {e}")
