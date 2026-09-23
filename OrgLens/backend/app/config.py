import os
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / 'backend' / '.env')

def number(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default

@dataclass
class Settings:
    api_key: str = field(default_factory=lambda: os.getenv('OPENAI_API_KEY', ''), repr=False)
    model: str = field(default_factory=lambda: os.getenv('OPENAI_MODEL', 'gpt-4.1-mini'))
    max_calls: int = field(default_factory=lambda: number('OPENAI_MAX_CALLS_PER_RUN', 40))
    max_concurrency: int = field(default_factory=lambda: number('OPENAI_MAX_CONCURRENCY', 2))
    max_document_chars: int = field(default_factory=lambda: number('MAX_DOCUMENT_CHARS', 120000))
    max_analysis_chars: int = field(default_factory=lambda: number('MAX_ANALYSIS_CHARS', 240000))
    extraction_batch_chars: int = field(default_factory=lambda: number('EXTRACTION_BATCH_CHARS', 18000))
    comparison_batch_size: int = field(default_factory=lambda: number('COMPARISON_BATCH_SIZE', 16))
    max_output_tokens: int = field(default_factory=lambda: number('OPENAI_MAX_OUTPUT_TOKENS', 16000))
    timeout_seconds: int = field(default_factory=lambda: number('OPENAI_TIMEOUT_SECONDS', 90))
    max_file_mb: int = field(default_factory=lambda: number('MAX_FILE_MB', 20))
    max_files_per_version: int = field(default_factory=lambda: number('MAX_FILES_PER_VERSION', 12))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('DATA_DIR', str(ROOT / 'data'))).resolve())
    prompt_version: str = 'orglens-v1.1'
