from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    clip_api_key: str = 'change-me'
    clip_storage_root: Path = Path('./storage')
    database_url: str = 'sqlite:///./clip.db'
    redis_url: str = 'redis://localhost:6379/0'
    clip_language: str = 'ru'
    clip_min_seconds: int = 15
    clip_max_seconds: int = 90
    clip_count: int = 5
    whisper_model: str = 'small'
    clip_whisper_device: str = 'auto'
    clip_whisper_compute_type: str = 'auto'
    clip_video_encoder: str = 'auto'
    clip_require_gpu: bool = False
    clip_max_upload_mb: int = 2048
    clip_download_timeout: int = 1800
    clip_min_free_mb: int = 2048
    clip_ffmpeg_timeout: int = 3600
    clip_face_tracking: bool = True
    clip_face_sample_seconds: float = 1.0
    clip_subtitle_font: str = 'DejaVu Sans'
    clip_subtitle_font_size: int = 18
    clip_retention_days: int = 30
    clip_stale_minutes: int = 120
    clip_max_attempts: int = 4
    clip_llm_base_url: str = ''
    clip_llm_api_key: str = ''
    clip_llm_model: str = 'gpt-6-luna'
    clip_vision_model: str = 'gpt-6-luna'
    clip_perlapenna_base_url: str = ''
    clip_perlapenna_api_key: str = ''
    clip_preview_codec: str = 'h264'
    clip_ollama_url: str = 'http://127.0.0.1:11434'
    clip_ollama_model: str = 'qwen3-vl:4b-instruct'
    clip_piper_model: Path = Path('./models/ru_RU-denis-medium.onnx')
    clip_voice_gain: float = 1.6
    clip_voiceover_queue: bool = False

settings = Settings()
settings.clip_storage_root.mkdir(parents=True, exist_ok=True)
