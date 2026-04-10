
MAX_CHARS = 10000

"""
Configuration management for SDX Agent
"""

from dataclasses import dataclass
from typing import Optional
import os
from dotenv import load_dotenv


@dataclass
class Config:
    """Central configuration"""
    
    # API Configuration
    gemini_api_key: str
    model_name: str = "gemini-2.5-flash"
    
    # Agent Configuration
    temperature: float = 0.7
    max_iterations: int = 20
    timeout: int = 300
    
    # Directory Configuration
    session_dir: str = "sessions"
    log_dir: str = "logs"
    cache_dir: str = ".cache"
    
    # Feature Flags
    enable_logging: bool = True
    enable_caching: bool = True
    verbose_default: bool = False
    
    # UI Configuration
    theme_color: str = "#FF8C42"
    show_token_usage: bool = True
    
    @classmethod
    def from_env(cls) -> 'Config':
        """Load configuration from environment variables"""
        load_dotenv()
        
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set")
        
        return cls(
            gemini_api_key=api_key,
            model_name=os.getenv("MODEL_NAME", "gemini-2.5-flash"),
            temperature=float(os.getenv("TEMPERATURE", 0.7)),
            max_iterations=int(os.getenv("MAX_ITERATIONS", 20)),
            timeout=int(os.getenv("TIMEOUT", 300)),
            session_dir=os.getenv("SESSION_DIR", "sessions"),
            log_dir=os.getenv("LOG_DIR", "logs"),
            enable_logging=os.getenv("ENABLE_LOGGING", "true").lower() == "true",
            enable_caching=os.getenv("ENABLE_CACHING", "true").lower() == "true",
        )


def get_config() -> Config:
    """Get global configuration instance"""
    return Config.from_env()
