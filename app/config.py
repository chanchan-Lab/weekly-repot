import json
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv


def ids(key):
    return {int(x.strip()) for x in os.getenv(key, '').split(',') if x.strip()}


@dataclass
class Config:
    token: str
    chats: set[int]
    users: set[int]
    admins: set[int]
    members: dict[int, str]
    mode: str
    model: str
    key: str
    data: Path
    template: Path
    timezone: ZoneInfo
    max_input: int
    max_team: int

    @classmethod
    def load(cls):
        load_dotenv()
        cfg = cls(
            os.getenv('TELEGRAM_BOT_TOKEN', ''), ids('ALLOWED_CHAT_IDS'),
            ids('ALLOWED_USER_IDS'), ids('ADMIN_USER_IDS'),
            {int(k): str(v) for k, v in json.loads(os.getenv('TEAM_MEMBERS_JSON', '{}')).items()},
            os.getenv('AI_MODE', 'mock'), os.getenv('OPENAI_MODEL', ''),
            os.getenv('OPENAI_API_KEY', ''), Path(os.getenv('DATA_DIR', 'data')),
            Path(os.getenv('TEMPLATE_PATH', 'templates/weekly-template.pptx')),
            ZoneInfo(os.getenv('TZ', 'Asia/Seoul')),
            int(os.getenv('MAX_INPUT_CHARS', '3500')), int(os.getenv('MAX_TEAM_CHARS', '30000')),
        )
        if not cfg.token:
            raise ValueError('TELEGRAM_BOT_TOKEN을 .env에 입력하세요.')
        if cfg.mode not in {'mock', 'openai'}:
            raise ValueError('AI_MODE는 mock 또는 openai입니다.')
        if cfg.mode == 'openai' and (not cfg.model or not cfg.key):
            raise ValueError('openai 모드는 OPENAI_API_KEY와 OPENAI_MODEL이 필요합니다.')
        cfg.data.mkdir(parents=True, exist_ok=True)
        return cfg
