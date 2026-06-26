"""Telegram bot. Commands: /start /dashboard /report /analyze /chat /help"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from telegram import Update  # noqa: E402
from telegram.constants import ChatAction, ParseMode  # noqa: E402
from telegram.ext import (  # noqa: E402
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)

from src.config import get_config  # noqa: E402
from src.db import init_db, get_report  # noqa: E402
from src.pipeline import analyze_ticker, build_dashboard_md  # noqa: E402
from src.analyzer import analyze  # noqa: E402
from src.data_fetcher import fetch_snapshot  # noqa: E402
from src.news_fetcher import fetch_news  # noqa: E402

logger = logging.getLogger(__name__)


# ============ Handlers ============

async def start_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *DSA-HK Bot*\n\n"
        "港股 AI 智能分析系統。\n\n"
        "*指令列表:*\n"
        "/dashboard — 今日決策儀表板\n"
        "/report \\<代碼\\> — 個股深度報告 (例: `/report 0700.HK`)\n"
        "/analyze \\<代碼\\> — 重新分析個股\n"
        "/chat \\<代碼\\> \\<問題\\> — 對個股提問\n"
        "/help — 顯示說明\n",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *DSA-HK Bot 說明*\n\n"
        "• `/dashboard` — 今日所有港股的決策儀表板摘要\n"
        "• `/report 0700.HK` — 取得騰訊控股的最新完整報告（含新聞、技術指標、操作建議）\n"
        "• `/analyze 0700.HK` — 即時重新分析單一 ticker\n"
        "• `/chat 0700.HK 今日走勢如何？` — 對 AI 問股，支援多輪對話\n\n"
        "💡 *提示*: 所有報告基於當前 LLM 模型生成，僅供參考，不構成投資建議。",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def dashboard_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_config()
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    md = build_dashboard_md(language=cfg.report_language)
    # Telegram message limit 4096 chars; truncate if needed
    if len(md) > 4000:
        md = md[:4000] + "\n\n_... 報告過長已截斷，請到 Web UI 查看完整內容_"
    # Escape Telegram reserved chars
    md_escaped = _escape_md(md)
    await update.message.reply_text(md_escaped, parse_mode=ParseMode.MARKDOWN_V2)


async def report_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_config()
    if not ctx.args:
        await update.message.reply_text("請提供 ticker 代碼，例如: /report 0700.HK")
        return
    code = ctx.args[0].upper()
    if not re.match(r"^\d{4,5}\.HK$", code):
        await update.message.reply_text("格式錯誤，請用 4-5 位數字 + .HK，例如: 0700.HK")
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)
    report = get_report(code)
    if not report:
        await update.message.reply_text(
            f"❌ 沒有 {code} 的今日報告。\n\n用 `/analyze {code}` 即時分析。"
        )
        return

    md = report["full_md"]
    # Telegram message limit
    if len(md) > 4000:
        # Send as file attachment
        from io import BytesIO
        bio = BytesIO(md.encode("utf-8"))
        bio.name = f"{code}_{report['report_date']}.md"
        await update.message.reply_document(
            document=bio,
            caption=f"📄 {code} 完整報告 ({report['report_date']})",
        )
    else:
        await update.message.reply_text(_escape_md(md), parse_mode=ParseMode.MARKDOWN_V2)


async def analyze_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_config()
    if not ctx.args:
        await update.message.reply_text("請提供 ticker 代碼，例如: /analyze 0700.HK")
        return
    code = ctx.args[0].upper()
    if not re.match(r"^\d{4,5}\.HK$", code):
        await update.message.reply_text("格式錯誤，請用 4-5 位數字 + .HK，例如: 0700.HK")
        return

    await update.message.reply_text(f"⏳ 正在分析 {code}...")
    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    # Run in executor to avoid blocking the event loop
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, analyze_ticker, code, True, cfg.report_language)
    except Exception as e:
        await update.message.reply_text(f"❌ 分析失敗: {e}")
        return

    if not result:
        await update.message.reply_text(f"❌ {code} 分析失敗，請檢查 logs/")
        return

    # Fetch the report we just saved
    report = get_report(code)
    if report and report.get("full_md"):
        md = report["full_md"]
        if len(md) > 4000:
            from io import BytesIO
            bio = BytesIO(md.encode("utf-8"))
            bio.name = f"{code}_{report['report_date']}.md"
            await update.message.reply_document(
                document=bio,
                caption=f"✅ {code} 分析完成 · 評分 {result.score} · {result.operation_advice}",
            )
        else:
            await update.message.reply_text(_escape_md(md), parse_mode=ParseMode.MARKDOWN_V2)
    else:
        await update.message.reply_text(
            f"✅ {code} 完成\n"
            f"評分: {result.score}\n"
            f"建議: {result.operation_advice}\n"
            f"摘要: {result.summary}"
        )


async def chat_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = get_config()
    if len(ctx.args) < 2:
        await update.message.reply_text(
            "格式: /chat <代碼> <問題>\n例如: /chat 0700.HK 今日走勢如何？"
        )
        return
    code = ctx.args[0].upper()
    question = " ".join(ctx.args[1:])
    if not re.match(r"^\d{4,5}\.HK$", code):
        await update.message.reply_text("格式錯誤")
        return

    await ctx.bot.send_chat_action(update.effective_chat.id, ChatAction.TYPING)

    # Fetch snapshot + news
    snap = await asyncio.get_event_loop().run_in_executor(None, fetch_snapshot, code)
    if not snap:
        await update.message.reply_text(f"❌ 無法取得 {code} 的行情數據")
        return

    news = await asyncio.get_event_loop().run_in_executor(
        None, fetch_news, code, snap.get("name_zh"), snap.get("name_en"), 3, 7
    )

    # Build chat prompt (simpler than full analysis)
    from .prompts import get_prompts
    system_prompt, _ = get_prompts(cfg.report_language)
    chat_system = system_prompt + "\n\n你正在與用戶對話，回答要簡潔精準 (3-5句)。"

    closes = [k["close"] for k in snap.get("kline_30d", [])]
    news_summary = "\n".join(f"- {n['title']}" for n in news[:5]) or "(無近期新聞)"

    user_prompt = f"""用戶問題: {question}

【{code} {snap.get('name_zh', '')} 當前數據】
- 現價: {snap.get('last_price')} HKD ({snap.get('change_pct')}%)
- MA20/50/100/200: {snap.get('ma20')} / {snap.get('ma50')} / {snap.get('ma100')} / {snap.get('ma200')}
- RSI14: {snap.get('rsi14')}
- 52w高/低: {snap.get('52w_high')} / {snap.get('52w_low')}
- PE: {snap.get('pe_ttm')}, PB: {snap.get('pb')}
- 最近30日收盤: {', '.join(str(c) for c in closes[-15:])}

【近期新聞】
{news_summary}

請用繁體中文回答 (除非用戶用英文)，簡潔 3-5 句。"""

    try:
        import litellm
        # Set API keys
        import os
        if cfg.minimax_api_key:
            os.environ["MINIMAX_API_KEY"] = cfg.minimax_api_key
        if cfg.gemini_api_key:
            os.environ["GEMINI_API_KEY"] = cfg.gemini_api_key
        if cfg.deepseek_api_key:
            os.environ["DEEPSEEK_API_KEY"] = cfg.deepseek_api_key
        if cfg.openai_api_key:
            os.environ["OPENAI_API_KEY"] = cfg.openai_api_key

        model = cfg.resolve_litellm_model()
        call_kwargs = cfg.resolve_llm_call_kwargs()
        response = litellm.completion(
            model=model,
            messages=[
                {"role": "system", "content": chat_system},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.5,
            max_tokens=600,
            timeout=60,
            **call_kwargs,
        )
        answer = response.choices[0].message.content.strip()
        await update.message.reply_text(answer)
    except Exception as e:
        await update.message.reply_text(f"❌ AI 對話失敗: {e}")


async def error_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    logger.warning(f"Update {update} caused error: {ctx.error}")


# ============ Markdown escaping ============

def _escape_md(text: str) -> str:
    """Escape Telegram MarkdownV2 reserved chars."""
    # MarkdownV2 reserved: _ * [ ] ( ) ~ ` > # + - = | { } . !
    reserved = r"_*[]()~`>#+-=|{}.!"
    return re.sub(f"([{re.escape(reserved)}])", r"\\\1", text)


# ============ Main ============

def run_bot() -> None:
    cfg = get_config()
    if not cfg.has_telegram():
        logger.error("Telegram not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
        return

    init_db()

    app = Application.builder().token(cfg.telegram_bot_token).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("dashboard", dashboard_cmd))
    app.add_handler(CommandHandler("report", report_cmd))
    app.add_handler(CommandHandler("analyze", analyze_cmd))
    app.add_handler(CommandHandler("chat", chat_cmd))
    app.add_error_handler(error_handler)

    logger.info("Starting Telegram bot...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


def send_message(text: str) -> bool:
    """Send a message to the configured Telegram chat. Used by scheduler."""
    cfg = get_config()
    if not cfg.has_telegram():
        return False
    try:
        from telegram import Bot
        bot = Bot(token=cfg.telegram_bot_token)
        kwargs = {"chat_id": cfg.telegram_chat_id, "text": text[:4000]}
        if cfg.telegram_message_thread_id:
            kwargs["message_thread_id"] = cfg.telegram_message_thread_id
        bot.send_message(**kwargs)
        return True
    except Exception as e:
        logger.error(f"Telegram send failed: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_bot()
