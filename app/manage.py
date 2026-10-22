import argparse
import asyncio
from datetime import datetime
from pathlib import Path
import sqlite3
import zipfile
from .config import Config
from .ppt import inspect_template, TOKENS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['delete-webhook', 'check-template', 'backup'])
    args = parser.parse_args()
    cfg = Config.load()
    if args.command == 'delete-webhook':
        from telegram import Bot
        async def delete():
            async with Bot(cfg.token) as bot:
                await bot.delete_webhook(drop_pending_updates=False)
        asyncio.run(delete())
        print('Webhook 해제 완료. Polling을 사용할 수 있습니다.')
    elif args.command == 'check-template':
        found = inspect_template(cfg.template)
        print('발견된 토큰:', sorted(found))
        missing = {'{{COMPLETED}}', '{{NEXT_WEEK}}', '{{ISSUES}}'} - found
        if missing or found - TOKENS:
            raise SystemExit(f'누락={sorted(missing)}, 미지원={sorted(found-TOKENS)}')
        print('토큰 검사 통과. 시각적 넘침 검사는 별도로 필요합니다.')
    else:
        source = cfg.data / 'weekly.sqlite3'
        if not source.exists():
            raise SystemExit('DB가 없습니다. 봇을 먼저 실행하세요.')
        snapshot = cfg.data / 'backup-snapshot.sqlite3'
        with sqlite3.connect(source) as src, sqlite3.connect(snapshot) as dst:
            src.backup(dst)
        archive = cfg.data / 'backup.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot, 'weekly.sqlite3')
            for path in sorted((cfg.data / 'reports').rglob('*')):
                if path.is_file():
                    z.write(path, path.relative_to(cfg.data))
        snapshot.unlink()
        print(f'백업 생성: {archive} ({datetime.now().isoformat()})')


if __name__ == '__main__':
    main()
