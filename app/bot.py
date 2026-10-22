import asyncio
import json
import logging
from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler
from .config import Config
from .db import DB
from .report import week_start, summarize
from .ppt import render

log = logging.getLogger('weekly_bot')

HELP = '''주간업무 봇
/whoami : 현재 대화방·사용자 ID
/submit [YYYY-MM-DD] 업무내용 : 해당 주 업무 저장 (재제출은 전체 교체)
/mine [YYYY-MM-DD] : 내 제출 원문
/status [YYYY-MM-DD] : 팀 제출 현황
/ppt [YYYY-MM-DD] : PPT 생성·반환 (관리자 전용)
날짜 생략 시 한국시간 이번 주 월요일 기준입니다.
그룹에서는 /submit@봇이름 처럼 명령에 봇 이름을 붙이세요.
업무·보고서는 이 그룹 구성원에게 공개됩니다.
'''


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    # HTTP URL에 Bot Token이 포함되므로 라이브러리 HTTP 로그를 출력하지 않음
    for name in ('httpx', 'httpcore', 'telegram', 'openai'):
        logging.getLogger(name).setLevel(logging.CRITICAL)
    cfg = Config.load()
    db = DB(cfg.data / 'weekly.sqlite3')
    lock = asyncio.Lock()

    async def whoami(update, context):
        if update.effective_user and update.effective_message:
            await update.effective_message.reply_text(
                f'chat_id={update.effective_chat.id}\nuser_id={update.effective_user.id}')

    async def allowed(update, admin=False):
        if not update.effective_user or not update.effective_message:
            return False
        user, chat = update.effective_user.id, update.effective_chat.id
        ok = chat in cfg.chats and user in cfg.users and (not admin or user in cfg.admins)
        if not ok:
            await update.effective_message.reply_text('권한이 없습니다. /whoami의 ID를 관리자에게 전달하세요.')
        return ok

    def parse(update):
        text = update.effective_message.text or ''
        rest = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ''
        first = rest.split(maxsplit=1)[0] if rest else ''
        if first and first[0].isdigit() and len(first) >= 5 and first[4] == '-':
            week = week_start(first, cfg.timezone)
            body = rest[len(first):].strip()
        else:
            week, body = week_start(timezone=cfg.timezone), rest
        return week, body

    async def help_command(update, context):
        if await allowed(update):
            await update.effective_message.reply_text(HELP)

    async def submit(update, context):
        if not await allowed(update):
            return
        week, body = parse(update)
        if not body or len(body) > cfg.max_input:
            await update.effective_message.reply_text(f'명령 뒤에 1~{cfg.max_input}자의 업무 내용을 입력하세요.')
            return
        user = update.effective_user
        db.submit(update.effective_chat.id, week, user.id, cfg.members.get(user.id, user.full_name), body, update.update_id)
        await update.effective_message.reply_text(f'{week} 주 업무 저장 완료. 재제출하면 기존 내용 전체를 교체합니다.')

    async def mine(update, context):
        if not await allowed(update):
            return
        week, _ = parse(update)
        rows = db.list(update.effective_chat.id, week)
        row = next((r for r in rows if r['user_id'] == update.effective_user.id), None)
        await update.effective_message.reply_text(row['body'] if row else '제출 내역이 없습니다.')

    async def status(update, context):
        if not await allowed(update):
            return
        week, _ = parse(update)
        rows = db.list(update.effective_chat.id, week)
        submitted = {r['user_id']: r['name'] for r in rows}
        members = cfg.members | submitted
        lines = [f'{name}: ' + ('제출' if uid in submitted else '미제출') for uid, name in members.items()]
        text = f'{week} 주 제출 현황\n' + '\n'.join(lines)
        for i in range(0, len(text), 3500):
            await update.effective_message.reply_text(text[i:i+3500])

    async def ppt(update, context):
        if not await allowed(update, admin=True):
            return
        week, _ = parse(update)
        if lock.locked():
            await update.effective_message.reply_text('다른 PPT를 생성 중입니다. 완료 후 다시 요청하세요.')
            return
        async with lock:
            rows = db.list(update.effective_chat.id, week)
            if not rows:
                await update.effective_message.reply_text('해당 주차의 제출 내역이 없습니다.')
                return
            if not cfg.template.is_file():
                await update.effective_message.reply_text('먼저 templates/weekly-template.pptx 양식을 준비하세요. README를 확인해 주세요.')
                return
            job_id = f'{update.effective_chat.id}_{update.update_id}'
            if not db.job(job_id, update.effective_chat.id, week):
                await update.effective_message.reply_text('이미 처리한 요청입니다. 재생성하려면 새 /ppt 명령을 보내세요.')
                return
            try:
                await update.effective_message.reply_text('PPT 초안을 생성합니다. 완료 후 사실관계와 글자 넘침을 확인해 주세요.')
                report = await summarize(rows, cfg)
                folder = cfg.data / 'reports' / job_id
                folder.mkdir(parents=True, exist_ok=True)
                (folder / 'source.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
                (folder / 'report.json').write_text(report.model_dump_json(indent=2), encoding='utf-8')
                path = folder / f'weekly_{week}.pptx'
                await asyncio.to_thread(render, cfg.template, path, report, rows, week, cfg.mode)
                with path.open('rb') as f:
                    await update.effective_message.reply_document(
                        document=f, filename=path.name,
                        caption='검토용 초안입니다. 사실관계·누락·레이아웃을 확인하세요.' +
                        (' AI 미사용 원문 테스트 모드입니다.' if cfg.mode == 'mock' else ''),
                        read_timeout=60, write_timeout=60)
                db.finish(job_id, 'sent')
            except Exception as exc:
                db.finish(job_id, 'failed', type(exc).__name__)
                # 예외 내용에 키·원문이 포함될 수 있어 타입만 로그로 남김
                log.error('generation failed job=%s type=%s', job_id, type(exc).__name__)
                await update.effective_message.reply_text(
                    f'생성/전송 실패 ({type(exc).__name__}). 설정·양식·API 연결을 확인 후 새 /ppt로 재시도하세요.')

    async def error(update, context):
        log.error('handler failed type=%s', type(context.error).__name__)
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text('처리 실패. 날짜는 YYYY-MM-DD 형식으로 입력하고 설정을 확인하세요.')

    async def post_init(app):
        if (await app.bot.get_webhook_info()).url:
            raise RuntimeError('기존 Webhook을 먼저 해제하세요. README 참고.')
        await app.bot.set_my_commands([BotCommand(name, desc) for name, desc in [
            ('start', '사용 안내'), ('help', '사용 안내'), ('whoami', '내 ID 확인'),
            ('submit', '업무 제출'), ('mine', '내 원문'), ('status', '제출 현황'), ('ppt', 'PPT 초안 생성')]])

    app = (Application.builder().token(cfg.token).concurrent_updates(8)
           .post_init(post_init).build())
    for name, handler in [('whoami', whoami), ('start', help_command), ('help', help_command),
                          ('submit', submit), ('mine', mine), ('status', status), ('ppt', ppt)]:
        app.add_handler(CommandHandler(name, handler))
    app.add_error_handler(error)
    app.run_polling(allowed_updates=['message'], drop_pending_updates=False, bootstrap_retries=3)


if __name__ == '__main__':
    main()
