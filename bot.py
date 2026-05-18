import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from config import BOT_TOKEN, ALLOWED_USERS
import db

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── состояния (хранятся в user_data["state"]) ───────────────────────────────
ST_IDLE      = "idle"
ST_AMOUNT    = "ask_amount"
ST_DESC      = "ask_desc"
ST_NICK      = "ask_nick"
ST_MSG       = "ask_msg"

# ─── клавиатуры ──────────────────────────────────────────────────────────────

def main_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💸 Я должен",    callback_data="action:iowe"),
            InlineKeyboardButton("💳 Мне должны",  callback_data="action:heowe"),
        ],
        [
            InlineKeyboardButton("⚖️ Баланс",      callback_data="action:balance"),
            InlineKeyboardButton("📋 История",     callback_data="action:history"),
        ],
        [
            InlineKeyboardButton("💼 Взаиморасчёт", callback_data="action:settle"),
            InlineKeyboardButton("🔍 Проверка",    callback_data="action:verify"),
        ],
        [
            InlineKeyboardButton("🏷 Псевдонимы",  callback_data="action:nicks"),
            InlineKeyboardButton("✉️ Написать",    callback_data="action:msg"),
        ],
    ])

BACK_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("◀️ Меню", callback_data="action:menu")]
])

def confirm_settle_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, закрыть", callback_data="settle:yes"),
            InlineKeyboardButton("❌ Отмена",      callback_data="action:menu"),
        ]
    ])

def skip_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➡️ Пропустить", callback_data="action:skip_desc"),
            InlineKeyboardButton("❌ Отмена",     callback_data="action:cancel"),
        ]
    ])

def nick_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🗑 Удалить псевдоним", callback_data="action:nick_clear"),
            InlineKeyboardButton("❌ Отмена",            callback_data="action:cancel"),
        ]
    ])

def cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отмена", callback_data="action:cancel")]
    ])

# ─── helpers ─────────────────────────────────────────────────────────────────

def username(update: Update) -> str:
    return (update.effective_user.username or "").lower()

def other_user(me: str) -> str:
    for u in ALLOWED_USERS:
        if u.lower() != me.lower():
            return u
    return "?"

def is_allowed(update: Update) -> bool:
    return username(update) in {u.lower() for u in ALLOWED_USERS}

def fmt_amount(amount: float) -> str:
    return f"{amount:,.2f}".rstrip("0").rstrip(".") + " ₽"

def disp(uname: str, nicks: dict[str, str] | None = None) -> str:
    if nicks is None:
        nicks = db.get_nicks()
    return nicks.get(uname.lower(), f"@{uname}")

async def deny(update: Update):
    await update.effective_message.reply_text("⛔ У тебя нет доступа к этому боту.")

def reset(ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    ctx.user_data["state"] = ST_IDLE

async def send_menu(message: Message, text: str = "Выбери действие:"):
    await message.reply_text(text, reply_markup=main_menu_kb())

async def edit_menu(query, text: str = "Выбери действие:"):
    await query.edit_message_text(text, reply_markup=main_menu_kb())

# ─── /start ──────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return await deny(update)
    reset(ctx)
    me     = username(update)
    db.save_chat_id(me, update.effective_chat.id)
    friend = other_user(me)
    nicks  = db.get_nicks()
    await send_menu(
        update.message,
        f"👋 Привет, {disp(me, nicks)}!\nНапарник: {disp(friend, nicks)}\n\nВыбери действие:"
    )

# ─── роутер кнопок ───────────────────────────────────────────────────────────

async def button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if not is_allowed(update):
        await query.edit_message_text("⛔ Нет доступа.")
        return

    data = query.data

    # ── навигация ──
    if data == "action:menu":
        reset(ctx)
        await edit_menu(query)

    elif data == "action:cancel":
        reset(ctx)
        await edit_menu(query, "🚫 Отменено.\n\nВыбери действие:")

    # ── начало ввода долга ──
    elif data in ("action:iowe", "action:heowe"):
        me     = username(update)
        friend = other_user(me)
        if data == "action:iowe":
            ctx.user_data["payer"]  = friend
            ctx.user_data["debtor"] = me
            prompt = f"💸 Сколько ты должен {disp(friend)}?\n\nВведи сумму:"
        else:
            ctx.user_data["payer"]  = me
            ctx.user_data["debtor"] = friend
            prompt = f"💳 Сколько должен тебе {disp(friend)}?\n\nВведи сумму:"
        ctx.user_data["state"] = ST_AMOUNT
        await query.edit_message_text(prompt, reply_markup=cancel_kb())

    # ── пропустить описание ──
    elif data == "action:skip_desc":
        if ctx.user_data.get("state") == ST_DESC:
            await _save_and_show(query, ctx, desc="")

    # ── баланс ──
    elif data == "action:balance":
        debtor, creditor, amount = db.get_net_balance()
        nicks = db.get_nicks()
        if amount == 0:
            text = "🟢 Никто никому не должен!"
        else:
            text = f"⚖️ {disp(debtor, nicks)} должен {disp(creditor, nicks)} {fmt_amount(amount)}"
        await query.edit_message_text(text, reply_markup=BACK_KB)

    # ── история ──
    elif data == "action:history":
        rows  = db.get_history(15)
        nicks = db.get_nicks()
        if not rows:
            await query.edit_message_text("📭 История пуста.", reply_markup=BACK_KB)
            return

        lines = ["📋 Последние операции:\n"]
        for tx_id, payer, debtor, amount, desc, created_at, chain_hash in rows:
            label = f" — {desc}" if desc else ""
            date  = created_at[:16]
            short = chain_hash[:8]
            lines.append(
                f"#{tx_id} [{date}] `{short}`\n"
                f"  {disp(debtor, nicks)} → {disp(payer, nicks)} {fmt_amount(amount)}{label}"
            )

        del_kb = [[InlineKeyboardButton(f"🗑 #{tx_id}", callback_data=f"del:{tx_id}")]
                  for tx_id, *_ in rows[:5]]
        del_kb.append([InlineKeyboardButton("◀️ Меню", callback_data="action:menu")])

        await query.edit_message_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup(del_kb),
            parse_mode="Markdown",
        )

    # ── удаление записи ──
    elif data.startswith("del:"):
        tx_id   = int(data.split(":")[1])
        deleted = db.delete_transaction(tx_id)
        msg = f"🗑 Запись #{tx_id} удалена." if deleted else f"❌ Запись #{tx_id} не найдена."
        await query.edit_message_text(msg, reply_markup=BACK_KB)

    # ── взаиморасчёт ──
    elif data == "action:settle":
        debtor, creditor, amount = db.get_net_balance()
        nicks = db.get_nicks()
        if amount == 0:
            await query.edit_message_text("🟢 Баланс нулевой — нечего закрывать.", reply_markup=BACK_KB)
        else:
            await query.edit_message_text(
                f"💼 {disp(debtor, nicks)} должен {disp(creditor, nicks)} {fmt_amount(amount)}.\n\n"
                "Провести взаиморасчёт и обнулить баланс?",
                reply_markup=confirm_settle_kb(),
            )

    elif data == "settle:yes":
        amount = db.settle_all()
        if amount:
            await query.edit_message_text(
                f"✅ Взаиморасчёт на {fmt_amount(amount)}. Баланс обнулён!",
                reply_markup=BACK_KB,
            )
        else:
            await query.edit_message_text("🟢 Баланс уже был нулевым.", reply_markup=BACK_KB)

    # ── проверка цепочки ──
    elif data == "action:verify":
        errors = db.verify_chain()
        if not errors:
            rows = db.get_history(1)
            tip  = f"`{rows[0][6][:16]}…`" if rows else "—"
            await query.edit_message_text(
                f"✅ Цепочка целая.\nХэш последней записи: {tip}",
                reply_markup=BACK_KB,
                parse_mode="Markdown",
            )
        else:
            lines = ["⚠️ Нарушения целостности!\n"]
            for err in errors:
                lines.append(f"  • {err}")
            await query.edit_message_text("\n".join(lines), reply_markup=BACK_KB)

    # ── сообщение другу ──
    elif data == "action:msg":
        me     = username(update)
        friend = other_user(me)
        friend_chat_id = db.get_chat_id(friend)
        if not friend_chat_id:
            await query.edit_message_text(
                f"⚠️ {disp(friend)} ещё не запускал бота — некуда отправлять.",
                reply_markup=BACK_KB,
            )
            return
        ctx.user_data["state"] = ST_MSG
        await query.edit_message_text(
            f"✉️ Напиши сообщение или отправь фото для {disp(friend)}:",
            reply_markup=cancel_kb(),
        )

    # ── псевдонимы ──
    elif data == "action:nicks":
        me     = username(update)
        friend = other_user(me)
        nicks  = db.get_nicks()
        me_n     = nicks.get(me, "не задан")
        friend_n = nicks.get(friend.lower(), "не задан")
        ctx.user_data["state"] = ST_NICK
        await query.edit_message_text(
            f"🏷 Псевдонимы:\n\n"
            f"  @{me} → {me_n}\n"
            f"  @{friend} → {friend_n}\n\n"
            "Напиши новый псевдоним для себя:",
            reply_markup=nick_kb(),
        )

    elif data == "action:nick_clear":
        me = username(update)
        db.clear_nick(me)
        reset(ctx)
        await query.edit_message_text(f"✅ Псевдоним @{me} удалён.", reply_markup=BACK_KB)

# ─── роутер сообщений ────────────────────────────────────────────────────────

async def message_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return await deny(update)

    db.save_chat_id(username(update), update.effective_chat.id)
    state = ctx.user_data.get("state", ST_IDLE)

    if state == ST_AMOUNT:
        await _process_amount(update, ctx)
    elif state == ST_DESC:
        await _process_desc(update, ctx)
    elif state == ST_NICK:
        await _process_nick(update, ctx)
    elif state == ST_MSG:
        await _process_msg(update, ctx)
    else:
        await send_menu(update.message)


async def photo_router(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отдельный роутер для фото — работает только в состоянии ST_MSG."""
    if not is_allowed(update):
        return await deny(update)

    db.save_chat_id(username(update), update.effective_chat.id)

    if ctx.user_data.get("state") == ST_MSG:
        await _process_msg(update, ctx)
    else:
        await send_menu(update.message)

# ─── ввод суммы ──────────────────────────────────────────────────────────────

async def _process_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().replace(",", ".").replace(" ", "")
    try:
        amount = float(text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Некорректная сумма. Введи число больше 0:", reply_markup=cancel_kb())
        return

    ctx.user_data["amount"] = amount
    ctx.user_data["state"]  = ST_DESC
    await update.message.reply_text("📝 За что? Напиши описание:", reply_markup=skip_kb())

# ─── ввод описания ───────────────────────────────────────────────────────────

async def _process_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text.strip()
    # Отправляем фейковый query-объект не нужен — используем message напрямую
    payer  = ctx.user_data["payer"]
    debtor = ctx.user_data["debtor"]
    amount = ctx.user_data["amount"]

    db.add_transaction(payer, debtor, amount, desc)
    nicks = db.get_nicks()
    label = f" ({desc})" if desc else ""
    reset(ctx)

    await update.message.reply_text(
        f"✅ {disp(debtor, nicks)} должен {disp(payer, nicks)} {fmt_amount(amount)}{label}",
        reply_markup=main_menu_kb(),
    )

async def _save_and_show(query, ctx: ContextTypes.DEFAULT_TYPE, desc: str):
    payer  = ctx.user_data["payer"]
    debtor = ctx.user_data["debtor"]
    amount = ctx.user_data["amount"]

    db.add_transaction(payer, debtor, amount, desc)
    nicks = db.get_nicks()
    label = f" ({desc})" if desc else ""
    reset(ctx)

    await query.edit_message_text(
        f"✅ {disp(debtor, nicks)} должен {disp(payer, nicks)} {fmt_amount(amount)}{label}",
        reply_markup=main_menu_kb(),
    )

# ─── отправка сообщения другу ────────────────────────────────────────────────

async def _process_msg(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    me     = username(update)
    friend = other_user(me)
    nicks  = db.get_nicks()
    bot    = update.get_bot()

    friend_chat_id = db.get_chat_id(friend)
    if not friend_chat_id:
        reset(ctx)
        await update.message.reply_text(
            f"⚠️ {disp(friend, nicks)} ещё не запускал бота.",
            reply_markup=main_menu_kb(),
        )
        return

    header = f"✉️ {disp(me, nicks)}:"

    if update.message.photo:
        # Берём фото наилучшего качества (последний элемент списка)
        photo_id = update.message.photo[-1].file_id
        caption  = update.message.caption or ""
        full_caption = f"{header}\n\n{caption}" if caption else header
        await bot.send_photo(
            chat_id=friend_chat_id,
            photo=photo_id,
            caption=full_caption,
        )
    else:
        text = update.message.text.strip()
        await bot.send_message(
            chat_id=friend_chat_id,
            text=f"{header}\n\n{text}",
        )

    reset(ctx)
    await update.message.reply_text("✅ Сообщение отправлено.", reply_markup=main_menu_kb())


# ─── ввод псевдонима ─────────────────────────────────────────────────────────

async def _process_nick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    new_nick = update.message.text.strip()

    if len(new_nick) > 32:
        await update.message.reply_text("❌ Псевдоним не может быть длиннее 32 символов.", reply_markup=nick_kb())
        return

    me = username(update)
    db.set_nick(me, new_nick)
    reset(ctx)

    nicks  = db.get_nicks()
    friend = other_user(me)
    await update.message.reply_text(
        f"✅ Псевдонимы обновлены:\n\n"
        f"  Ты: {disp(me, nicks)}\n"
        f"  Напарник: {disp(friend, nicks)}",
        reply_markup=main_menu_kb(),
    )

# ─── main ────────────────────────────────────────────────────────────────────

def main():
    db.init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_router))
    app.add_handler(MessageHandler(filters.PHOTO, photo_router))

    logger.info("Бот запущен. Разрешённые пользователи: %s", ALLOWED_USERS)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
