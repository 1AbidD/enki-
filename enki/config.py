import copy
import json
from pathlib import Path
from typing import Any, Dict

import os

CONFIG_FILE = Path(__file__).resolve().parent.parent / "config.json"

def get_data_dir() -> Path:
    """Mengambil direktori data aktif (mendukung override via env var ENKI_DATA_DIR)."""
    env_val = os.environ.get("ENKI_DATA_DIR")
    if env_val:
        p = Path(env_val).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(__file__).resolve().parent.parent / "data"

def get_log_dir() -> Path:
    """Mengambil direktori log aktif (mendukung override via env var ENKI_LOG_DIR)."""
    env_val = os.environ.get("ENKI_LOG_DIR")
    if env_val:
        p = Path(env_val).resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    return Path(__file__).resolve().parent.parent / "logs"

DEFAULT_CONFIG: Dict[str, Any] = {
    "model": "qwen2.5:3b",
    "num_ctx": 8192,
    "temperature": 0.2,
    "ollama_url": "http://127.0.0.1:11434",
    "is_muted": True,
    "passive_recall": False,
    "tts_engine": "supertonic",
    "tts_voice": "Microsoft Andika",
    "tts_voice_supertonic": "M4",
    "tts_model_dir": str(Path.home() / ".cache" / "supertonic3"),
    "tts_pronunciation": {"ENKI": "Enki"},
    "tts_synth_c0": 1.599501,
    "tts_synth_c1": 0.329724,
    "tts_audio_d0": 0.596618,
    "tts_audio_d1": 0.061480,
    "tts_chars_per_sec": 16.265423,
    "tts_gapless_start": True,
    "tts_max_start_delay": 6.0,
    "web_timeout": 15.0,
    "web_max_bytes": 1572864,
    "web_max_chars": 6000,
    "workspace_root": str(Path(__file__).resolve().parent.parent)
}

class AppConfig:
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config_path = config_path
        self._data: Dict[str, Any] = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            default_copy = copy.deepcopy(DEFAULT_CONFIG)
            self._save(default_copy)
            return default_copy
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Pastikan key default tersedia
                for k, v in DEFAULT_CONFIG.items():
                    if k not in data:
                        data[k] = copy.deepcopy(v)
                return data
        except Exception:
            return copy.deepcopy(DEFAULT_CONFIG)

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

    @property
    def passive_recall(self) -> bool:
        return bool(self._data.get("passive_recall", False))

    @property
    def tts_engine(self) -> str:
        return str(self._data.get("tts_engine", "supertonic"))

    @property
    def tts_voice(self) -> str:
        return self._data.get("tts_voice", "")

    def set_tts_voice(self, voice_name: str) -> None:
        self._data["tts_voice"] = voice_name
        self._save(self._data)

    @property
    def tts_voice_supertonic(self) -> str:
        return str(self._data.get("tts_voice_supertonic", "M4"))

    @property
    def tts_model_dir(self) -> str:
        return str(self._data.get("tts_model_dir", str(Path.home() / ".cache" / "supertonic3")))

    @property
    def tts_pronunciation(self) -> Dict[str, str]:
        val = self._data.get("tts_pronunciation", {"ENKI": "Enki"})
        if isinstance(val, dict):
            return dict(val)
        return {"ENKI": "Enki"}

    @property
    def tts_synth_c0(self) -> float:
        return float(self._data.get("tts_synth_c0", 1.599501))

    @property
    def tts_synth_c1(self) -> float:
        return float(self._data.get("tts_synth_c1", 0.329724))

    @property
    def tts_audio_d0(self) -> float:
        return float(self._data.get("tts_audio_d0", 0.596618))

    @property
    def tts_audio_d1(self) -> float:
        return float(self._data.get("tts_audio_d1", 0.061480))

    @property
    def tts_chars_per_sec(self) -> float:
        return float(self._data.get("tts_chars_per_sec", 16.265423))

    @property
    def tts_gapless_start(self) -> bool:
        return bool(self._data.get("tts_gapless_start", True))

    @property
    def tts_max_start_delay(self) -> float:
        return float(self._data.get("tts_max_start_delay", 6.0))

    @property
    def web_timeout(self) -> float:
        return float(self._data.get("web_timeout", 15.0))

    @property
    def web_max_bytes(self) -> int:
        return int(self._data.get("web_max_bytes", 1572864))

    @property
    def web_max_chars(self) -> int:
        return int(self._data.get("web_max_chars", 6000))

    @property
    def workspace_root(self) -> Path:
        raw = os.environ.get("ENKI_WORKSPACE_ROOT") or self._data.get("workspace_root", str(Path(__file__).resolve().parent.parent))
        return Path(raw).resolve()

    def set_mute(self, muted: bool) -> None:
        self._data["is_muted"] = muted
        self._save(self._data)

    def set_model(self, model_name: str) -> None:
        self._data["model"] = model_name
        self._save(self._data)

    def to_dict(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)

