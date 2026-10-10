import json
from pathlib import Path
from typing import Any, Dict

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "model": "qwen2.5:3b",
    "num_ctx": 8192,
    "temperature": 0.2,
    "ollama_url": "http://127.0.0.1:11434",
    "is_muted": False
}

class AppConfig:
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config_path = config_path
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            self._save(DEFAULT_CONFIG)
            return DEFAULT_CONFIG.copy()
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Pastikan key default tersedia
                for k, v in DEFAULT_CONFIG.items():
                    data.setdefault(k, v)
                return data
        except Exception:
            return DEFAULT_CONFIG.copy()

    def _save(self, data: Dict[str, Any]) -> None:
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    @property
    def model(self) -> str:
        return self._data.get("model", "qwen2.5:3b")

    @property
    def num_ctx(self) -> int:
        return int(self._data.get("num_ctx", 8192))

    @property
    def temperature(self) -> float:
        return float(self._data.get("temperature", 0.2))

    @property
    def ollama_url(self) -> str:
        return self._data.get("ollama_url", "http://127.0.0.1:11434")

    @property
    def is_muted(self) -> bool:
        return bool(self._data.get("is_muted", False))

    def set_mute(self, muted: bool) -> None:
        self._data["is_muted"] = muted
        self._save(self._data)

    def to_dict(self) -> Dict[str, Any]:
        return self._data.copy()
