"""
Configuration Management Module
"""

import os
from pathlib import Path
from typing import Optional
import toml
from pydantic_settings import BaseSettings
from pydantic import Field, field_validator
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class AppSettings(BaseSettings):
    """Application-wide settings from environment and config files"""
    
    # OpenRouter
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(
        default="https://openrouter.ai/api/v1",
        alias="OPENROUTER_BASE_URL"
    )
    llm_model: str = Field(default="bytedance/seedance-2.0", alias="LLM_MODEL")
    llm_provider: str = Field(default="openrouter", alias="LLM_PROVIDER")
    
    # YouTube API
    youtube_client_id: str = Field(default="", alias="YOUTUBE_CLIENT_ID")
    youtube_client_secret: str = Field(default="", alias="YOUTUBE_CLIENT_SECRET")

    # Reddit API (trend research)
    reddit_client_id: str = Field(default="", alias="REDDIT_CLIENT_ID")
    reddit_client_secret: str = Field(default="", alias="REDDIT_CLIENT_SECRET")
    youtube_developer_key: str = Field(default="", alias="YOUTUBE_DEVELOPER_KEY")
    
    # Stock Video APIs
    pexels_api_key: str = Field(default="", alias="PEXELS_API_KEY")
    pixabay_api_key: str = Field(default="", alias="PIXABAY_API_KEY")
    coverr_api_key: str = Field(default="", alias="COVERR_API_KEY")
    vimeo_access_token: str = Field(default="", alias="VIMEO_ACCESS_TOKEN")
    unsplash_api_key: str = Field(default="", alias="UNSPLASH_API_KEY")
    stock_video_sources: str = Field(default="pexels,pixabay,youtube_cc,coverr", alias="STOCK_VIDEO_SOURCES")
    enable_coverr: bool = Field(default=True, alias="ENABLE_COVERR")
    
    # Voice & Audio
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")
    tts_provider: str = Field(default="", alias="TTS_PROVIDER")
    voice_id: str = Field(default="Rachel", alias="VOICE_ID")
    elevenlabs_voice_id: str = Field(default="", alias="ELEVENLABS_VOICE_ID")
    elevenlabs_model_id: str = Field(default="eleven_flash_v2_5", alias="ELEVENLABS_MODEL_ID")
    elevenlabs_language_code: str = Field(default="", alias="ELEVENLABS_LANGUAGE_CODE")
    voice_language: str = Field(default="vi", alias="VOICE_LANGUAGE")
    
    # Paths
    temp_dir: str = Field(default="./temp", alias="TEMP_DIR")
    output_dir: str = Field(default="./outputs", alias="OUTPUT_DIR")
    config_file: str = Field(default="./config/config.toml", alias="CONFIG_FILE")
    
    # AI image / video cost controls
    together_api_key: str = Field(default="", alias="TOGETHER_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    stability_api_key: str = Field(default="", alias="STABILITY_API_KEY")
    seedance_api_key: str = Field(default="", alias="SEEDANCE_API_KEY")
    video_budget_usd: float = Field(default=1.00, alias="VIDEO_BUDGET_USD")
    seedance_max_clips: int = Field(default=2, alias="SEEDANCE_MAX_CLIPS")
    seedance_max_duration_s: int = Field(default=5, alias="SEEDANCE_MAX_DURATION_S")
    seedance_cost_per_s: float = Field(default=0.10, alias="SEEDANCE_COST_PER_S")
    ai_image_cost: float = Field(default=0.00, alias="AI_IMAGE_COST")

    # General
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    debug: bool = Field(default=False, alias="DEBUG")
    video_output_format: str = Field(default="mp4", alias="VIDEO_OUTPUT_FORMAT")
    max_video_length: int = Field(default=3600, alias="MAX_VIDEO_LENGTH")

    # Autopilot / review workflow
    autopilot_duration_s: int = Field(default=75, alias="AUTOPILOT_DURATION_S")
    autopilot_mode: str = Field(default="edl", alias="AUTOPILOT_MODE")
    autopilot_trend_query_limit: int = Field(default=2, alias="AUTOPILOT_TREND_QUERY_LIMIT")
    autopilot_trend_cache_hours: int = Field(default=12, alias="AUTOPILOT_TREND_CACHE_HOURS")
    autopilot_review_email_to: str = Field(default="", alias="AUTOPILOT_REVIEW_EMAIL_TO")
    autopilot_review_email_from: str = Field(default="", alias="AUTOPILOT_REVIEW_EMAIL_FROM")
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=587, alias="SMTP_PORT")
    smtp_username: str = Field(default="", alias="SMTP_USERNAME")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_use_tls: bool = Field(default=True, alias="SMTP_USE_TLS")

    @field_validator("debug", mode="before")
    @classmethod
    def parse_debug(cls, value):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "debug"}
        return value

    @field_validator("enable_coverr", mode="before")
    @classmethod
    def parse_enable_coverr(cls, value):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return value

    @field_validator("smtp_use_tls", mode="before")
    @classmethod
    def parse_smtp_use_tls(cls, value):
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return value
    
    class Config:
        env_file = ".env"
        case_sensitive = False


class ConfigManager:
    """Manage application configuration from files and environment"""
    
    def __init__(self, config_file: Optional[str] = None):
        self.settings = AppSettings()
        self.config_file = config_file or self.settings.config_file
        self.toml_config = {}
        
        if os.path.exists(self.config_file):
            self._load_toml_config()
    
    def _load_toml_config(self):
        """Load configuration from TOML file"""
        try:
            with open(self.config_file, 'r') as f:
                self.toml_config = toml.load(f)
        except Exception as e:
            from loguru import logger
            logger.warning(f"Could not load config file {self.config_file}: {e}")
    
    def get(self, key: str, section: Optional[str] = None, default=None):
        """
        Get configuration value with fallback order:
        1. Environment variable (uppercase)
        2. TOML file
        3. Default value
        """
        # Try environment variable first
        env_value = os.getenv(key.upper())
        if env_value is not None:
            return env_value
        
        # Try TOML file
        if section and section in self.toml_config:
            if key in self.toml_config[section]:
                return self.toml_config[section][key]
        
        # Try TOML without section
        if key in self.toml_config:
            return self.toml_config[key]
        
        # Return default
        return default
    
    def create_dirs(self):
        """Create necessary directories"""
        Path(self.settings.temp_dir).mkdir(parents=True, exist_ok=True)
        Path(self.settings.output_dir).mkdir(parents=True, exist_ok=True)
        Path("logs").mkdir(parents=True, exist_ok=True)


# Global config manager instance
config = ConfigManager()
