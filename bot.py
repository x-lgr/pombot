import asyncio
import html
import logging
import os
import random
import re
import sqlite3
import string
from io import BytesIO
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    import qrcode
except ImportError:  # pragma: no cover - runtime fallback if optional dependency is absent.
    qrcode = None


load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("BOT_DATA_DIR", BASE_DIR / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "premium_bot.sqlite"
USERS_TXT = DATA_DIR / "users.txt"
BACKUP_TXT = DATA_DIR / "users.txt.bak"

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = {
    int(part.strip())
    for part in os.getenv("ADMIN_IDS", "").split(",")
    if part.strip().isdigit()
}

DEFAULT_START_MESSAGE = "Welcome {first_name}"
DEFAULT_START_IMAGE_FILE_ID = ""
CAPTION_LIMIT = 1024
PAYMENT_TEMPLATE = "https://redirect-beta-lemon.vercel.app/{upiname}/{bankname}/{amount}"
INVITE_LINK_MIN_SECONDS = 60
INVITE_LINK_DAILY_LIMIT = 10

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("premium-bot")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def db() -> Any:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # OneDrive and some shared hosts can reject SQLite's rollback-journal rename.
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                age_verified INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS start_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                position INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                price INTEGER NOT NULL,
                description TEXT NOT NULL,
                duration TEXT,
                image_file_id TEXT,
                button_url TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS discounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                percent INTEGER NOT NULL,
                code TEXT NOT NULL UNIQUE,
                original_amount INTEGER NOT NULL,
                final_amount INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payment_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                plan_id INTEGER NOT NULL,
                original_amount INTEGER NOT NULL,
                discount_code TEXT,
                discount_percent INTEGER DEFAULT 0,
                final_amount INTEGER NOT NULL,
                utr TEXT NOT NULL,
                message TEXT,
                screenshot_file_id TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS premium_users (
                user_id INTEGER PRIMARY KEY,
                plan_id INTEGER NOT NULL,
                purchase_date TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invite_link_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                invite_link TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS broadcast_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent INTEGER NOT NULL,
                failed INTEGER NOT NULL,
                total INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )

        defaults = {
            "start_message": DEFAULT_START_MESSAGE,
            "start_image_file_id": DEFAULT_START_IMAGE_FILE_ID,
            "buy_button_name": "💎 Buy Premium",
            "demo_button_name": "🎬 Watch Demo",
            "demo_url": "",
            "upi_id": "",
            "receiver_name": "",
            "payment_url": PAYMENT_TEMPLATE,
            "upiname": "",
            "bankname": "",
            "premium_channel_id": "",
            "demo_channel_url": "",
            "contact_admin": "",
        }
        for key, value in defaults.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES(?, ?)",
                (key, value),
            )
        try:
            conn.execute("ALTER TABLE users ADD COLUMN age_verified INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE plans ADD COLUMN image_file_id TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE plans ADD COLUMN button_url TEXT")
        except sqlite3.OperationalError:
            pass


def get_setting(key: str, default: str = "") -> str:
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def is_admin(user_id: Optional[int]) -> bool:
    return bool(user_id and user_id in ADMIN_IDS)


async def deny(update: Update) -> None:
    if update.message:
        await update.message.reply_text("❌ Access Denied")
    elif update.callback_query:
        await update.callback_query.answer("❌ Access Denied", show_alert=True)


def valid_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def user_placeholders(text: str, user: Any) -> str:
    values = {
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "username": f"@{user.username}" if user.username else "",
        "user_id": str(user.id),
    }
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


async def send_text_with_format_fallback(message: Any, text: str, reply_markup: Any = None) -> None:
    for parse_mode in (ParseMode.HTML, ParseMode.MARKDOWN_V2, None):
        try:
            await message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
            return
        except BadRequest:
            continue
    await message.reply_text(html.escape(text), reply_markup=reply_markup)


async def send_photo_with_format_fallback(message: Any, photo: str, caption: str, reply_markup: Any = None) -> None:
    for parse_mode in (ParseMode.HTML, ParseMode.MARKDOWN_V2, None):
        try:
            await message.reply_photo(photo, caption=caption, parse_mode=parse_mode, reply_markup=reply_markup)
            return
        except BadRequest:
            continue
    await message.reply_photo(photo, reply_markup=reply_markup)


def start_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    buy_name = get_setting("buy_button_name", "💎 Buy Premium")
    demo_name = get_setting("demo_button_name", "🎬 Watch Demo")
    demo_url = get_setting("demo_url") or get_setting("demo_channel_url")
    first_row = [InlineKeyboardButton(buy_name, callback_data="buy")]
    if demo_url and valid_url(demo_url):
        first_row.append(InlineKeyboardButton(demo_name, url=demo_url))
    rows.append(first_row)

    with db() as conn:
        buttons = conn.execute(
            "SELECT id, name, url FROM start_buttons ORDER BY position, id"
        ).fetchall()
    for button in buttons:
        rows.append([InlineKeyboardButton(button["name"], url=button["url"])])
    return InlineKeyboardMarkup(rows)


def quick_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["💎 Buy Premium", "🎬 Watch Demo"],
            ["⭐ Premium", "📊 Status"],
            ["📚 Help"],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


async def enable_quick_reply_keyboard(message: Any) -> None:
    await message.reply_text("Quick menu enabled.", reply_markup=quick_reply_keyboard())


def plans_keyboard() -> InlineKeyboardMarkup:
    with db() as conn:
        plans = conn.execute(
            "SELECT id, name FROM plans ORDER BY sort_order, id"
        ).fetchall()
    buttons = [InlineKeyboardButton(p["name"], callback_data=f"plan:{p['id']}") for p in plans]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    back_button = InlineKeyboardButton("🔴 Back", callback_data="back:start")
    if len(buttons) % 2 == 1 and rows:
        rows[-1].append(back_button)
    else:
        rows.append([back_button])
    return InlineKeyboardMarkup(rows)


def payment_keyboard(plan_id: int, amount: int, discounted: bool = False) -> InlineKeyboardMarkup:
    url = make_payment_url(amount)
    rows = []
    if url:
        rows.append([InlineKeyboardButton(f"🟢 Pay ₹{amount} via UPI", url=url)])
    cancel_callback = "cancelpay" if discounted else f"cancel:{plan_id}"
    rows.append(
        [
            InlineKeyboardButton("🔴 Cancel", callback_data=cancel_callback),
            InlineKeyboardButton("I Have Paid", callback_data=f"paid:{plan_id}"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def make_payment_url(amount: int) -> str:
    upiname = get_setting("upiname")
    bankname = get_setting("bankname")
    template = get_setting("payment_url", PAYMENT_TEMPLATE)
    if not upiname or not bankname or amount <= 0:
        return ""
    return template.format(upiname=upiname, bankname=bankname, amount=amount)


def add_user_to_txt(user_id: int) -> None:
    USERS_TXT.touch(exist_ok=True)
    existing = {
        line.strip()
        for line in USERS_TXT.read_text(encoding="utf-8").splitlines()
        if line.strip().isdigit()
    }
    existing.add(str(user_id))
    ordered = sorted(existing, key=lambda value: int(value))
    tmp = USERS_TXT.with_suffix(".tmp")
    tmp.write_text("\n".join(ordered) + ("\n" if ordered else ""), encoding="utf-8")
    if USERS_TXT.exists():
        BACKUP_TXT.write_text(USERS_TXT.read_text(encoding="utf-8"), encoding="utf-8")
    tmp.replace(USERS_TXT)


def track_user(user: Any) -> None:
    if not user:
        return
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, username, first_name, last_name, age_verified, created_at, updated_at)
            VALUES(?, ?, ?, ?, 0, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_name = excluded.last_name,
                updated_at = excluded.updated_at
            """,
            (user.id, user.username, user.first_name, user.last_name, now_iso(), now_iso()),
        )
    add_user_to_txt(user.id)


def is_age_verified(user_id: int) -> bool:
    with db() as conn:
        row = conn.execute("SELECT age_verified FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return bool(row and row["age_verified"])


def set_age_verified(user_id: int, verified: bool) -> None:
    with db() as conn:
        conn.execute(
            "UPDATE users SET age_verified = ?, updated_at = ? WHERE user_id = ?",
            (1 if verified else 0, now_iso(), user_id),
        )


def age_gate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Yes, I am 18+", callback_data="age:yes")],
            [InlineKeyboardButton("No, I am under 18", callback_data="age:no")],
        ]
    )


async def ask_age_confirmation(message: Any) -> None:
    await message.reply_text(
        "Age Confirmation\n\n"
        "Is bot ko use karne ke liye confirm karein ki aap 18+ hain.",
        reply_markup=age_gate_keyboard(),
    )


async def send_start_menu(message: Any, user: Any) -> None:
    text = user_placeholders(get_setting("start_message", DEFAULT_START_MESSAGE), user)
    image_id = get_setting("start_image_file_id")
    keyboard = start_keyboard()
    if image_id:
        if len(text) <= CAPTION_LIMIT:
            await send_photo_with_format_fallback(message, image_id, text, keyboard)
        else:
            await message.reply_photo(image_id)
            await send_text_with_format_fallback(message, text, keyboard)
    else:
        await send_text_with_format_fallback(message, text, keyboard)
    await enable_quick_reply_keyboard(message)


async def ensure_age_access(update: Update) -> bool:
    user = update.effective_user
    if is_admin(user.id):
        return True
    if is_age_verified(user.id):
        return True
    if update.message:
        await ask_age_confirmation(update.message)
    elif update.callback_query:
        await update.callback_query.message.reply_text(
            "Pehle age confirmation complete karein. /start bhejein."
        )
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    track_user(user)
    if not is_admin(user.id) and not is_age_verified(user.id):
        await ask_age_confirmation(update.message)
        return
    await send_start_menu(update.message, user)


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update.effective_user)
    if not await ensure_age_access(update):
        return
    contact = get_setting("contact_admin")
    contact_line = f"\nContact admin: {html.escape(contact)}" if contact else ""
    await update.message.reply_text(
        "📚 Help Menu\n\n"
        "Available Commands:\n"
        "/start - Open the start menu\n"
        "/help - Show this help menu\n"
        "/premium - Premium user panel\n"
        "/status - Your account status\n\n"
        "Use the Buy Premium button to choose a plan, pay, and submit your screenshot, UTR, and message."
        f"{contact_line}",
        reply_markup=start_keyboard(),
    )


async def user_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update.effective_user)
    if not await ensure_age_access(update):
        return
    user = update.effective_user
    with db() as conn:
        premium = conn.execute(
            """
            SELECT p.name, pu.purchase_date, pu.status
            FROM premium_users pu
            JOIN plans p ON p.id = pu.plan_id
            WHERE pu.user_id = ?
            """,
            (user.id,),
        ).fetchone()
    if premium:
        account = (
            "⭐ PREMIUM USER\n"
            f"Plan: {premium['name']}\n"
            f"Purchase Date: {premium['purchase_date']}\n"
            f"Premium Status: {premium['status']}"
        )
    else:
        account = "FREE USER"
    await update.message.reply_text(
        f"User Name: {user.full_name}\n"
        f"Username: {'@' + user.username if user.username else '-'}\n"
        f"User ID: {user.id}\n"
        f"Account Type:\n{account}"
    )


async def premium(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update.effective_user)
    if not await ensure_age_access(update):
        return
    user = update.effective_user
    with db() as conn:
        premium_row = conn.execute(
            """
            SELECT p.name, pu.plan_id, pu.purchase_date, pu.status
            FROM premium_users pu
            JOIN plans p ON p.id = pu.plan_id
            WHERE pu.user_id = ?
            """,
            (user.id,),
        ).fetchone()
    if not premium_row:
        await update.message.reply_text("You are not a premium user yet.", reply_markup=start_keyboard())
        return
    await update.message.reply_text(
        "⭐ PREMIUM USER\n"
        f"Current Plan: {premium_row['name']}\n"
        f"Purchase Date: {premium_row['purchase_date']}\n"
        f"Status: {premium_row['status']}\n\n"
        "Invite link limit: 1 minute me 1 baar, 1 din me maximum 10 baar.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Generate Access Link", callback_data="accesslink")]]
        ),
    )


def parse_price(value: str) -> Optional[int]:
    match = re.search(r"\d+", value.replace(",", ""))
    return int(match.group(0)) if match else None


def is_skip_value(value: str) -> bool:
    return value.strip().lower() in {"-", "skip", "nochange", "no change"}


async def admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text or ""
    if not text.startswith("??"):
        if context.user_data.get("wizard"):
            await wizard_text(update, context)
        else:
            await quick_menu_text(update, context)
        return
    user_id = update.effective_user.id
    if not is_admin(user_id):
        await deny(update)
        return
    parts = text[2:].split(maxsplit=1)
    command = parts[0].lower() if parts else ""
    arg = parts[1] if len(parts) > 1 else ""
    handlers = {
        "setstart": lambda: begin_wizard(update, context, "setstart", "Send the new start message."),
        "viewstart": lambda: reply(update, get_setting("start_message", DEFAULT_START_MESSAGE)),
        "resetstart": lambda: set_and_reply(update, "start_message", DEFAULT_START_MESSAGE, "Start message reset."),
        "setstartimage": lambda: begin_wizard(update, context, "setstartimage", "Send a photo to use as the start image."),
        "viewstartimage": lambda: view_start_image(update),
        "removestartimage": lambda: set_and_reply(update, "start_image_file_id", "", "Start image removed."),
        "resetstartimage": lambda: set_and_reply(update, "start_image_file_id", DEFAULT_START_IMAGE_FILE_ID, "Start image reset."),
        "addbutton": lambda: begin_button_add(update, context),
        "buttons": lambda: list_buttons(update),
        "removebutton": lambda: begin_remove_button(update, context),
        "editbutton": lambda: begin_edit_button(update, context),
        "setbuybuttonname": lambda: set_arg_or_wizard(update, context, arg, "buy_button_name", "Send Buy button name."),
        "setdemobuttonname": lambda: set_arg_or_wizard(update, context, arg, "demo_button_name", "Send Demo button name."),
        "setdemourl": lambda: set_url_arg_or_wizard(update, context, arg, "demo_url", "Send demo URL."),
        "setdemochannel": lambda: set_url_arg_or_wizard(update, context, arg, "demo_channel_url", "Send demo channel URL."),
        "addplan": lambda: begin_plan_add(update, context),
        "plans": lambda: list_plans(update),
        "removeplan": lambda: begin_remove_plan(update, context),
        "editplan": lambda: begin_edit_plan(update, context),
        "setplanurl": lambda: set_plan_url_arg_or_wizard(update, context, arg),
        "setupi": lambda: set_arg_or_wizard(update, context, arg, "upi_id", "Send UPI ID."),
        "setupiname": lambda: set_arg_or_wizard(update, context, arg, "upiname", "Send UPI name."),
        "setreceivername": lambda: set_arg_or_wizard(update, context, arg, "receiver_name", "Send receiver name."),
        "setbankname": lambda: set_arg_or_wizard(update, context, arg, "bankname", "Send bank name."),
        "setpaymenturl": lambda: set_arg_or_wizard(update, context, arg, "payment_url", "Send payment URL template."),
        "setpremiumchannel": lambda: set_arg_or_wizard(update, context, arg, "premium_channel_id", "Send premium channel ID."),
        "broadcast": lambda: begin_broadcast(update, context),
        "backupusers": lambda: backup_users(update),
        "importusers": lambda: begin_import_users(update, context),
        "rebuilddb": lambda: rebuild_db(update),
        "help": lambda: admin_help(update),
        "status": lambda: admin_status(update, context),
        "stats": lambda: admin_stats(update),
    }
    result = handlers.get(command)
    if result:
        await result()
    else:
        await update.message.reply_text("Unknown admin command. Use ??help.")


async def quick_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()
    if text not in {"💎 Buy Premium", "🎬 Watch Demo", "⭐ Premium", "📊 Status", "📚 Help"}:
        return
    track_user(update.effective_user)
    if not await ensure_age_access(update):
        return
    if text == "💎 Buy Premium":
        await update.message.reply_text("Choose Your Plan", reply_markup=plans_keyboard())
    elif text == "🎬 Watch Demo":
        demo_url = get_setting("demo_url") or get_setting("demo_channel_url")
        if demo_url and valid_url(demo_url):
            await update.message.reply_text(
                "Demo open karein:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎬 Watch Demo", url=demo_url)]]),
            )
        else:
            await update.message.reply_text("Demo channel abhi configured nahi hai.")
    elif text == "⭐ Premium":
        await premium(update, context)
    elif text == "📊 Status":
        await user_status(update, context)
    elif text == "📚 Help":
        await help_cmd(update, context)


async def reply(update: Update, text: str) -> None:
    await update.message.reply_text(text or "-")


async def set_and_reply(update: Update, key: str, value: str, message: str) -> None:
    set_setting(key, value)
    await update.message.reply_text(message)


async def begin_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE, name: str, prompt: str) -> None:
    context.user_data["wizard"] = {"name": name, "step": 0}
    await update.message.reply_text(prompt)


async def set_arg_or_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE, arg: str, key: str, prompt: str) -> None:
    if arg:
        set_setting(key, arg.strip())
        await update.message.reply_text("Saved.")
    else:
        context.user_data["wizard"] = {"name": "set_setting", "key": key}
        await update.message.reply_text(prompt)


async def set_url_arg_or_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE, arg: str, key: str, prompt: str) -> None:
    if arg:
        if not valid_url(arg):
            await update.message.reply_text("❌ Invalid URL.")
            return
        set_setting(key, arg.strip())
        await update.message.reply_text("Saved.")
    else:
        context.user_data["wizard"] = {"name": "set_url_setting", "key": key}
        await update.message.reply_text(prompt)


async def set_plan_url_arg_or_wizard(update: Update, context: ContextTypes.DEFAULT_TYPE, arg: str) -> None:
    if arg:
        await update.message.reply_text("Use ??editplan to edit plan button_url.")
        return
    context.user_data["wizard"] = {"name": "set_plan_url", "step": "plan_id"}
    await update.message.reply_text("Send plan ID.")


async def view_start_image(update: Update) -> None:
    image_id = get_setting("start_image_file_id")
    if not image_id:
        await update.message.reply_text("No start image configured.")
        return
    await update.message.reply_photo(image_id, caption="Current start image")


async def begin_button_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["wizard"] = {"name": "addbutton", "step": "name"}
    await update.message.reply_text("Send button name.")


async def list_buttons(update: Update) -> None:
    with db() as conn:
        buttons = conn.execute("SELECT id, name, url, position FROM start_buttons ORDER BY position, id").fetchall()
    if not buttons:
        await update.message.reply_text("No buttons configured.")
        return
    await update.message.reply_text(
        "\n".join(f"ID {b['id']} | {b['name']} | {b['url']} | Position {b['position']}" for b in buttons)
    )


async def begin_remove_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await list_buttons(update)
    context.user_data["wizard"] = {"name": "removebutton"}
    await update.message.reply_text("Send button ID to delete.")


async def begin_edit_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await list_buttons(update)
    context.user_data["wizard"] = {"name": "editbutton", "step": "id"}
    await update.message.reply_text("Send button ID to edit.")


async def begin_plan_add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["wizard"] = {"name": "addplan", "step": "name", "data": {}}
    await update.message.reply_text("Send plan name.")


async def list_plans(update: Update) -> None:
    with db() as conn:
        plans = conn.execute("SELECT * FROM plans ORDER BY sort_order, id").fetchall()
    if not plans:
        await update.message.reply_text("No plans configured.")
        return
    await update.message.reply_text(
        "\n".join(
            f"ID {p['id']} | {p['name']} | ₹{p['price']} | {p['duration'] or '-'} | Sort {p['sort_order']}\n{p['description']}"
            for p in plans
        )
    )


async def begin_remove_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await list_plans(update)
    context.user_data["wizard"] = {"name": "removeplan"}
    await update.message.reply_text("Send plan ID to delete.")


async def begin_edit_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await list_plans(update)
    context.user_data["wizard"] = {"name": "editplan", "step": "id"}
    await update.message.reply_text(
        "Send plan ID to edit.\n\n"
        "During editing, you can type '-' or 'skip' at any step to keep the current value.\n"
        "For images, you can type 'remove' to delete the current image."
    )


async def begin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["wizard"] = {"name": "broadcast", "step": "content"}
    await update.message.reply_text("Send broadcast content. Text or photo with caption is supported.")


async def backup_users(update: Update) -> None:
    USERS_TXT.touch(exist_ok=True)
    await update.message.reply_document(InputFile(USERS_TXT), filename="users.txt")


async def begin_import_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["wizard"] = {"name": "importusers"}
    await update.message.reply_text("Upload users.txt as a document.")


async def rebuild_db(update: Update) -> None:
    USERS_TXT.touch(exist_ok=True)
    imported = skipped = 0
    with db() as conn:
        for line in USERS_TXT.read_text(encoding="utf-8").splitlines():
            user_id = line.strip()
            if not user_id.isdigit():
                skipped += 1
                continue
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO users(user_id, created_at, updated_at) VALUES(?, ?, ?)",
                (int(user_id), now_iso(), now_iso()),
            )
            imported += int(conn.total_changes > before)
    await update.message.reply_text(f"Imported: {imported} Skipped: {skipped}")


async def admin_help(update: Update) -> None:
    await update.message.reply_text(
        "Admin Help\n\n"
        "Start Message\n"
        "??setstart - New /start message set karein\n"
        "??viewstart - Current start message dekhein\n"
        "??resetstart - Default start message restore karein\n\n"
        "Start Image\n"
        "??setstartimage - /start image set karein\n"
        "??viewstartimage - Current start image preview karein\n"
        "??removestartimage - Start image remove karein\n"
        "??resetstartimage - Default start image restore karein\n\n"
        "Start Buttons\n"
        "??addbutton - Start menu me URL button add karein\n"
        "??editbutton - Existing button edit karein\n"
        "??removebutton - Button delete karein\n"
        "??buttons - Sab configured buttons dekhein\n"
        "??setbuybuttonname - Buy Premium button name set karein\n"
        "??setdemobuttonname - Demo button name set karein\n"
        "??setdemourl - Demo button URL set karein\n\n"
        "Plans\n"
        "??addplan - Premium plan create karein\n"
        "??editplan - Existing plan edit karein (use '-' or 'skip' to keep current values)\n"
        "??removeplan - Plan delete karein\n"
        "??plans - Sab plans list karein\n\n"
        "Payment Settings\n"
        "??setupi - UPI ID set karein\n"
        "??setupiname - Payment URL ke liye UPI name set karein\n"
        "??setreceivername - Receiver display name set karein\n"
        "??setbankname - Payment URL ke liye bank name set karein\n"
        "??setpaymenturl - Payment URL template set karein\n\n"
        "Channels\n"
        "??setpremiumchannel - Premium channel ID set karein\n"
        "??setdemochannel - Demo channel URL set karein\n\n"
        "Broadcast\n"
        "??broadcast - All users ko message/photo broadcast karein\n\n"
        "Users Backup\n"
        "??backupusers - users.txt file download karein\n"
        "??importusers - users.txt upload karke users import karein\n"
        "??rebuilddb - users.txt se database rebuild karein\n\n"
        "Status & Stats\n"
        "??status - Bot health/status check karein\n"
        "??stats - Users, payments, broadcasts stats dekhein\n"
        "??help - Ye admin help menu dekhein"
    )


async def admin_stats(update: Update) -> None:
    with db() as conn:
        total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        premium_users = conn.execute("SELECT COUNT(*) c FROM premium_users WHERE status='active'").fetchone()["c"]
        approved = conn.execute("SELECT COUNT(*) c FROM payment_tickets WHERE status='approved'").fetchone()["c"]
        rejected = conn.execute("SELECT COUNT(*) c FROM payment_tickets WHERE status='rejected'").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) c FROM payment_tickets WHERE status='pending'").fetchone()["c"]
        discounts = conn.execute("SELECT COUNT(*) c FROM discounts").fetchone()["c"]
        broadcasts = conn.execute("SELECT COUNT(*) c FROM broadcast_logs").fetchone()["c"]
        today = datetime.now(timezone.utc).date().isoformat()
        today_users = conn.execute("SELECT COUNT(*) c FROM users WHERE created_at LIKE ?", (today + "%",)).fetchone()["c"]
        week_start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        weekly_users = conn.execute("SELECT COUNT(*) c FROM users WHERE created_at >= ?", (week_start,)).fetchone()["c"]
        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(final_amount), 0) total FROM payment_tickets WHERE status = 'approved'"
        ).fetchone()["total"]
        today_revenue = conn.execute(
            """
            SELECT COALESCE(SUM(final_amount), 0) total
            FROM payment_tickets
            WHERE status = 'approved' AND COALESCE(reviewed_at, created_at) LIKE ?
            """,
            (today + "%",),
        ).fetchone()["total"]
        weekly_revenue = conn.execute(
            """
            SELECT COALESCE(SUM(final_amount), 0) total
            FROM payment_tickets
            WHERE status = 'approved' AND COALESCE(reviewed_at, created_at) >= ?
            """,
            (week_start,),
        ).fetchone()["total"]
    await update.message.reply_text(
        "📊 Stats\n"
        f"Total Users: {total_users}\n"
        f"Today's New Users: {today_users}\n"
        f"Weekly Users: {weekly_users}\n"
        f"Premium Users: {premium_users}\n"
        f"Total Revenue: ₹{total_revenue}\n"
        f"Today Revenue: ₹{today_revenue}\n"
        f"Weekly Revenue: ₹{weekly_revenue}\n"
        f"Total Approved Payments: {approved}\n"
        f"Total Rejected Payments: {rejected}\n"
        f"Pending Approvals: {pending}\n"
        f"Generated Discounts: {discounts}\n"
        f"Broadcast Statistics: {broadcasts}"
    )


async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    with db() as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        premium_count = conn.execute("SELECT COUNT(*) c FROM premium_users").fetchone()["c"]
        pending = conn.execute("SELECT COUNT(*) c FROM payment_tickets WHERE status='pending'").fetchone()["c"]
        plans = conn.execute("SELECT COUNT(*) c FROM plans").fetchone()["c"]
        broadcasts = conn.execute("SELECT COUNT(*) c FROM broadcast_logs").fetchone()["c"]
        today = datetime.now(timezone.utc).date().isoformat()
        week_start = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        total_revenue = conn.execute(
            "SELECT COALESCE(SUM(final_amount), 0) total FROM payment_tickets WHERE status = 'approved'"
        ).fetchone()["total"]
        today_revenue = conn.execute(
            """
            SELECT COALESCE(SUM(final_amount), 0) total
            FROM payment_tickets
            WHERE status = 'approved' AND COALESCE(reviewed_at, created_at) LIKE ?
            """,
            (today + "%",),
        ).fetchone()["total"]
        weekly_revenue = conn.execute(
            """
            SELECT COALESCE(SUM(final_amount), 0) total
            FROM payment_tickets
            WHERE status = 'approved' AND COALESCE(reviewed_at, created_at) >= ?
            """,
            (week_start,),
        ).fetchone()["total"]
    demo_ok = bool(get_setting("demo_channel_url") or get_setting("demo_url"))
    premium_channel = get_setting("premium_channel_id")
    upi_ok = bool(get_setting("upi_id") and get_setting("upiname") and get_setting("bankname"))
    db_ok = DB_PATH.exists()
    channel_ok = await check_channel_permissions(context, premium_channel)
    warnings = []
    if premium_channel and not channel_ok:
        warnings.append("❌ Warning: Premium Channel Permission Missing")
    if not upi_ok:
        warnings.append("❌ UPI Not Configured")
    if not db_ok:
        warnings.append("❌ Database Offline")
    await update.message.reply_text(
        "🤖 Bot Status\n"
        "Bot: Online ✅\n"
        f"Users: {users}\n"
        f"Premium Users: {premium_count}\n"
        f"Pending Payments: {pending}\n"
        f"Total Revenue: ₹{total_revenue}\n"
        f"Today Revenue: ₹{today_revenue}\n"
        f"Weekly Revenue: ₹{weekly_revenue}\n"
        f"Plans: {plans}\n"
        f"Broadcast Sent: {broadcasts}\n"
        f"Demo Channel: {'Configured ✅' if demo_ok else 'Missing ❌'}\n"
        f"Premium Channel: {'Configured ✅' if premium_channel else 'Missing ❌'}\n"
        f"UPI: {'Configured ✅' if upi_ok else 'Missing ❌'}\n"
        f"Database: {'Connected ✅' if db_ok else 'Offline ❌'}\n\n"
        + ("\n".join(warnings) if warnings else "No warnings."),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔵 Refresh", callback_data="adminstatus")]]),
    )


async def check_channel_permissions(context: ContextTypes.DEFAULT_TYPE, channel_id: str) -> bool:
    if not channel_id:
        return False
    try:
        member = await context.bot.get_chat_member(channel_id, context.bot.id)
        return member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
    except TelegramError:
        return False


async def wizard_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wizard = context.user_data.get("wizard")
    if not wizard:
        return
    if not is_admin(update.effective_user.id):
        await deny(update)
        return
    text = update.message.text or ""
    name = wizard["name"]
    if name == "setstart":
        set_setting("start_message", text)
        context.user_data.pop("wizard", None)
        await update.message.reply_text("Start message saved.")
    elif name == "set_setting":
        set_setting(wizard["key"], text.strip())
        context.user_data.pop("wizard", None)
        await update.message.reply_text("Saved.")
    elif name == "set_url_setting":
        if not valid_url(text):
            await update.message.reply_text("❌ Invalid URL. Send a valid http/https URL.")
            return
        set_setting(wizard["key"], text.strip())
        context.user_data.pop("wizard", None)
        await update.message.reply_text("Saved.")
    elif name == "set_plan_url":
        await set_plan_url_step(update, context, text)
    elif name == "addbutton":
        await addbutton_step(update, context, text)
    elif name == "removebutton":
        await remove_by_id(update, context, "start_buttons", text, "Button removed.")
    elif name == "editbutton":
        await editbutton_step(update, context, text)
    elif name == "addplan":
        await addplan_step(update, context, text)
    elif name == "removeplan":
        await remove_by_id(update, context, "plans", text, "Plan removed.")
    elif name == "editplan":
        await editplan_step(update, context, text)
    elif name == "broadcast":
        await broadcast_text_step(update, context, text)
    elif name == "broadcast_button":
        await broadcast_button_step(update, context, text)


async def set_plan_url_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    wizard = context.user_data["wizard"]
    if wizard["step"] == "plan_id":
        if not text.isdigit():
            await update.message.reply_text("Send a numeric plan ID.")
            return
        wizard["plan_id"] = int(text)
        wizard["step"] = "url"
        await update.message.reply_text("Send button URL for this plan.")
    else:
        if not valid_url(text):
            await update.message.reply_text("❌ Invalid URL.")
            return
        with db() as conn:
            conn.execute(
                "UPDATE plans SET button_url = ? WHERE id = ?",
                (text.strip(), wizard["plan_id"]),
            )
        context.user_data.pop("wizard", None)
        await update.message.reply_text("Plan button URL saved.")


async def addbutton_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    wizard = context.user_data["wizard"]
    if wizard["step"] == "name":
        if not text.strip():
            await update.message.reply_text("❌ Button name cannot be empty.")
            return
        wizard["button_name"] = text.strip()
        wizard["step"] = "url"
        await update.message.reply_text("Send button URL.")
    else:
        if not valid_url(text):
            await update.message.reply_text("❌ Invalid URL.")
            return
        with db() as conn:
            max_pos = conn.execute("SELECT COALESCE(MAX(position), 0) p FROM start_buttons").fetchone()["p"]
            conn.execute(
                "INSERT INTO start_buttons(name, url, position) VALUES(?, ?, ?)",
                (wizard["button_name"], text.strip(), max_pos + 1),
            )
        context.user_data.pop("wizard", None)
        await update.message.reply_text("Button saved.")


async def remove_by_id(update: Update, context: ContextTypes.DEFAULT_TYPE, table: str, text: str, ok: str) -> None:
    if not text.strip().isdigit():
        await update.message.reply_text("Send a numeric ID.")
        return
    with db() as conn:
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (int(text),))
    context.user_data.pop("wizard", None)
    await update.message.reply_text(ok)


async def editbutton_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    wizard = context.user_data["wizard"]
    if wizard["step"] == "id":
        if not text.isdigit():
            await update.message.reply_text("Send a numeric button ID.")
            return
        wizard["id"] = int(text)
        wizard["step"] = "name"
        await update.message.reply_text("Send new button name.")
    elif wizard["step"] == "name":
        if not text.strip():
            await update.message.reply_text("❌ Button name cannot be empty.")
            return
        wizard["name_value"] = text.strip()
        wizard["step"] = "url"
        await update.message.reply_text("Send new button URL.")
    else:
        if not valid_url(text):
            await update.message.reply_text("❌ Invalid URL.")
            return
        with db() as conn:
            conn.execute(
                "UPDATE start_buttons SET name = ?, url = ? WHERE id = ?",
                (wizard["name_value"], text.strip(), wizard["id"]),
            )
        context.user_data.pop("wizard", None)
        await update.message.reply_text("Button updated.")


async def addplan_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    wizard = context.user_data["wizard"]
    data = wizard["data"]
    step = wizard["step"]
    if step == "name":
        data["name"] = text.strip()
        wizard["step"] = "price"
        await update.message.reply_text("Send price.")
    elif step == "price":
        price = parse_price(text)
        if not price:
            await update.message.reply_text("Send a valid numeric price.")
            return
        data["price"] = price
        wizard["step"] = "description"
        await update.message.reply_text("Send description.")
    elif step == "description":
        data["description"] = text
        wizard["step"] = "duration"
        await update.message.reply_text("Send duration, or type - to skip.")
    elif step == "duration":
        data["duration"] = "" if text.strip() == "-" else text.strip()
        wizard["step"] = "image"
        await update.message.reply_text("Send plan image, or type - to skip.")
    elif step == "image":
        if text.strip() != "-":
            await update.message.reply_text("Please send a photo, or type - to skip.")
            return
        data["image_file_id"] = ""
        wizard["step"] = "sort_order"
        await update.message.reply_text("Send sort order.")
    else:
        sort_order = parse_price(text) or 0
        with db() as conn:
            conn.execute(
                "INSERT INTO plans(name, price, description, duration, image_file_id, sort_order) VALUES(?, ?, ?, ?, ?, ?)",
                (
                    data["name"],
                    data["price"],
                    data["description"],
                    data["duration"],
                    data.get("image_file_id", ""),
                    sort_order,
                ),
            )
        context.user_data.pop("wizard", None)
        await update.message.reply_text("Plan saved.")


async def editplan_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    wizard = context.user_data["wizard"]
    data = wizard.setdefault("data", {})
    
    if wizard["step"] == "id":
        if not text.isdigit():
            await update.message.reply_text("Send a numeric plan ID.")
            return
        plan_id = int(text)
        with db() as conn:
            plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan:
            await update.message.reply_text("Plan not found. Send a valid plan ID.")
            return
        wizard["id"] = plan_id
        # Store original plan data for reference
        wizard["original"] = dict(plan)
        wizard["step"] = "name"
        await update.message.reply_text(
            f"Current name: {plan['name']}\n"
            "Send new plan name, or type '-' / 'skip' to keep current."
        )
    elif wizard["step"] == "name":
        if is_skip_value(text):
            data["name"] = wizard["original"]["name"]
        else:
            if not text.strip():
                await update.message.reply_text("Plan name cannot be empty. Send '-' to skip.")
                return
            data["name"] = text.strip()
        wizard["step"] = "price"
        await update.message.reply_text(
            f"Current price: ₹{wizard['original']['price']}\n"
            "Send new price, or type '-' / 'skip' to keep current."
        )
    elif wizard["step"] == "price":
        if is_skip_value(text):
            data["price"] = wizard["original"]["price"]
        else:
            price = parse_price(text)
            if not price:
                await update.message.reply_text("Send a valid numeric price, or '-' to skip.")
                return
            data["price"] = price
        wizard["step"] = "description"
        await update.message.reply_text(
            f"Current description: {wizard['original']['description']}\n"
            "Send new description, or type '-' / 'skip' to keep current."
        )
    elif wizard["step"] == "description":
        if is_skip_value(text):
            data["description"] = wizard["original"]["description"]
        else:
            data["description"] = text
        wizard["step"] = "duration"
        await update.message.reply_text(
            f"Current duration: {wizard['original']['duration'] or 'Not set'}\n"
            "Send new duration, or type '-' / 'skip' to keep current."
        )
    elif wizard["step"] == "duration":
        if is_skip_value(text):
            data["duration"] = wizard["original"]["duration"] or ""
        else:
            data["duration"] = "" if text.strip() == "remove" else text.strip()
        wizard["step"] = "image"
        await update.message.reply_text(
            "Send new plan image (photo), or type '-' / 'skip' to keep current image,\n"
            "or type 'remove' to delete current image."
        )
    elif wizard["step"] == "image":
        if is_skip_value(text):
            data["image_file_id"] = wizard["original"]["image_file_id"] or ""
        elif text.strip().lower() == "remove":
            data["image_file_id"] = ""
        else:
            # This will be handled by photo upload
            if text.strip():
                await update.message.reply_text("Please send a photo, or type '-' to skip, 'remove' to delete.")
                return
            return
        wizard["step"] = "sort_order"
        await update.message.reply_text(
            f"Current sort order: {wizard['original']['sort_order']}\n"
            "Send new sort order, or type '-' / 'skip' to keep current."
        )
    elif wizard["step"] == "sort_order":
        if is_skip_value(text):
            data["sort_order"] = wizard["original"]["sort_order"]
        else:
            data["sort_order"] = parse_price(text) or 0
        # Final step - update the plan
        with db() as conn:
            conn.execute(
                """
                UPDATE plans
                SET name = ?, price = ?, description = ?, duration = ?, image_file_id = ?, sort_order = ?
                WHERE id = ?
                """,
                (
                    data["name"],
                    data["price"],
                    data["description"],
                    data["duration"],
                    data.get("image_file_id", ""),
                    data["sort_order"],
                    wizard["id"],
                ),
            )
        context.user_data.pop("wizard", None)
        context.user_data.pop("original", None)
        await update.message.reply_text("Plan updated successfully!")


async def broadcast_button_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    wizard = context.user_data["wizard"]
    payload = context.user_data.setdefault("broadcast", {})
    if wizard["step"] == "name":
        if not text.strip():
            await update.message.reply_text("Button name cannot be empty.")
            return
        payload["button_name"] = text.strip()
        wizard["step"] = "url"
        await update.message.reply_text("Send button URL.")
    else:
        if not valid_url(text):
            await update.message.reply_text("❌ Invalid URL.")
            return
        payload["button_url"] = text.strip()
        await update.message.reply_text(
            "Ready to send?",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🟢 Confirm & Send", callback_data="bcconfirm"), InlineKeyboardButton("🔴 Cancel", callback_data="bccancel")]]
            ),
        )


async def wizard_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wizard = context.user_data.get("wizard")
    if not wizard or not is_admin(update.effective_user.id):
        return
    if wizard["name"] == "setstartimage":
        if not update.message.photo:
            await update.message.reply_text("❌ Please send a valid photo.")
            return
        photo = update.message.photo[-1]
        set_setting("start_image_file_id", photo.file_id)
        context.user_data.pop("wizard", None)
        await update.message.reply_text("Start image saved.")
    elif wizard["name"] in {"addplan", "editplan"} and wizard.get("step") == "image":
        if not update.message.photo:
            await update.message.reply_text("Please send a valid photo, or type - to skip, remove to delete.")
            return
        data = wizard.setdefault("data", {})
        data["image_file_id"] = update.message.photo[-1].file_id
        wizard["step"] = "sort_order"
        if wizard["name"] == "addplan":
            prompt = "Send sort order."
        else:
            prompt = f"Current sort order: {wizard['original']['sort_order']}\nSend new sort order, or type '-' / 'skip' to keep current."
        await update.message.reply_text(prompt)
    elif wizard["name"] == "broadcast":
        context.user_data["broadcast"] = {
            "type": "photo",
            "file_id": update.message.photo[-1].file_id,
            "caption": update.message.caption or "",
        }
        wizard["step"] = "button?"
        await update.message.reply_photo(
            update.message.photo[-1].file_id,
            caption=update.message.caption or "",
        )
        await update.message.reply_text(
            "Add Button?",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("Yes", callback_data="bcbtn:yes"), InlineKeyboardButton("No", callback_data="bcbtn:no")]]
            ),
        )


async def wizard_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    wizard = context.user_data.get("wizard")
    if not wizard or wizard["name"] != "importusers" or not is_admin(update.effective_user.id):
        return
    doc = update.message.document
    file = await doc.get_file()
    data = await file.download_as_bytearray()
    imported = skipped = 0
    for raw in data.decode("utf-8", errors="ignore").splitlines():
        value = raw.strip()
        if not value.isdigit():
            skipped += 1
            continue
        with db() as conn:
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO users(user_id, created_at, updated_at) VALUES(?, ?, ?)",
                (int(value), now_iso(), now_iso()),
            )
            imported += int(conn.total_changes > before)
        add_user_to_txt(int(value))
    context.user_data.pop("wizard", None)
    await update.message.reply_text(f"Imported: {imported} Skipped: {skipped}")


async def broadcast_text_step(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    context.user_data["broadcast"] = {"type": "text", "text": text}
    context.user_data["wizard"]["step"] = "button?"
    await update.message.reply_text("This is how it will look.")
    await update.message.reply_text(text)
    await update.message.reply_text(
        "Add Button?",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("Yes", callback_data="bcbtn:yes"), InlineKeyboardButton("No", callback_data="bcbtn:no")]]
        ),
    )


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data or ""
    track_user(query.from_user)
    if data == "age:yes":
        set_age_verified(query.from_user.id, True)
        try:
            await query.message.delete()
        except TelegramError:
            pass
        await query.message.reply_text("Age confirmed. Access enabled.")
        await send_start_menu(query.message, query.from_user)
        return
    if data == "age:no":
        set_age_verified(query.from_user.id, False)
        try:
            await query.message.delete()
        except TelegramError:
            pass
        await query.message.reply_text(
            "Access denied. Is bot ko use karne ke liye 18+ hona zaroori hai.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return
    if not is_admin(query.from_user.id) and not is_age_verified(query.from_user.id):
        await query.message.reply_text("Pehle age confirmation complete karein. /start bhejein.")
        return
    if data == "buy":
        await query.message.reply_text("Choose Your Plan", reply_markup=plans_keyboard())
    elif data == "back:start":
        await query.message.reply_text("Welcome Back 👋", reply_markup=start_keyboard())
    elif data.startswith("plan:"):
        await show_plan(query, int(data.split(":")[1]))
    elif data.startswith("paypage:"):
        await show_payment(query, int(data.split(":")[1]))
    elif data.startswith("cancel:"):
        await make_discount(query, int(data.split(":")[1]))
    elif data == "cancelpay":
        await query.message.reply_text(
            "Payment cancelled. Aapka discount saved hai; dobara Buy Premium se wahi discounted price milega.",
            reply_markup=start_keyboard(),
        )
    elif data.startswith("paid:"):
        context.user_data["payment"] = {"plan_id": int(data.split(":")[1]), "step": "screenshot"}
        await query.message.reply_text("Send:\n1. Screenshot\n2. UTR Number\n3. Your Message\n\nPlease send the payment screenshot first.")
    elif data == "accesslink":
        await generate_access_link(query, context)
    elif data.startswith("approve:") or data.startswith("reject:"):
        if not is_admin(query.from_user.id):
            await deny(update)
            return
        await review_payment(query, data)
    elif data == "adminstatus":
        if not is_admin(query.from_user.id):
            await deny(update)
            return
        fake_update = Update(update.update_id, message=query.message)
        await admin_status(fake_update, context)
    elif data.startswith("bcbtn:"):
        await broadcast_button_choice(query, context, data.endswith("yes"))
    elif data == "bcconfirm":
        await send_broadcast(query, context)
    elif data == "bccancel":
        context.user_data.pop("wizard", None)
        context.user_data.pop("broadcast", None)
        await query.message.reply_text("Broadcast cancelled.")


async def show_plan(query: Any, plan_id: int) -> None:
    with db() as conn:
        plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        await query.message.reply_text("Plan not found.")
        return
    plan_text = (
        f"{plan['name']}\n"
        f"Price: ₹{plan['price']}\n"
        f"Description: {plan['description']}\n"
        f"Benefits: {plan['duration'] or 'Premium access'}"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🟢 Buy Now", callback_data=f"paypage:{plan_id}")],
            [InlineKeyboardButton("🔴 Back", callback_data="buy")],
        ]
    )
    if plan["image_file_id"]:
        await send_photo_with_format_fallback(query.message, plan["image_file_id"], plan_text, keyboard)
    else:
        await query.message.reply_text(plan_text, reply_markup=keyboard)


def latest_discount(user_id: int, plan_id: int) -> Optional[sqlite3.Row]:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM discounts WHERE user_id = ? AND plan_id = ? ORDER BY id DESC LIMIT 1",
            (user_id, plan_id),
        ).fetchone()


async def show_payment(query: Any, plan_id: int, discount: Optional[sqlite3.Row] = None) -> None:
    with db() as conn:
        plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        await query.message.reply_text("Plan not found.")
        return
    discount = discount or latest_discount(query.from_user.id, plan_id)
    amount = discount["final_amount"] if discount else plan["price"]
    payment_url = make_payment_url(amount)
    if not payment_url:
        await query.message.reply_text("❌ Payment system not configured.")
        return
    payment_text = (
        "Payment Page\n\n"
        f"UPI ID: {get_setting('upi_id')}\n"
        f"Receiver: {get_setting('receiver_name') or get_setting('upiname')}\n"
        f"Amount: ₹{amount}\n"
        f"Payment URL: {payment_url}"
    )
    if qrcode:
        qr = qrcode.make(payment_url)
        image = BytesIO()
        image.name = "payment_qr.png"
        qr.save(image, format="PNG")
        image.seek(0)
        await query.message.reply_photo(
            photo=image,
            caption=payment_text[:CAPTION_LIMIT],
            reply_markup=payment_keyboard(plan_id, amount, discounted=bool(discount)),
        )
        if len(payment_text) > CAPTION_LIMIT:
            await query.message.reply_text(payment_text)
    else:
        await query.message.reply_text(payment_text, reply_markup=payment_keyboard(plan_id, amount, discounted=bool(discount)))


async def make_discount(query: Any, plan_id: int) -> None:
    with db() as conn:
        plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        await query.message.reply_text("Plan not found.")
        return
    existing = latest_discount(query.from_user.id, plan_id)
    if existing:
        await query.message.reply_text(
            "Payment cancelled. Aapka discount already saved hai; dobara Buy Premium se wahi discounted price milega.",
            reply_markup=start_keyboard(),
        )
        return
    percent = random.randint(1, 25)
    final = max(1, round(plan["price"] * (100 - percent) / 100))
    code = "DISC" + str(percent) + "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
    with db() as conn:
        conn.execute(
            "INSERT INTO discounts(user_id, plan_id, percent, code, original_amount, final_amount, created_at) VALUES(?, ?, ?, ?, ?, ?, ?)",
            (query.from_user.id, plan_id, percent, code, plan["price"], final, now_iso()),
        )
    await query.message.reply_text(
        "Maybe the price feels a little high 😄\n\n"
        f"Congratulations!\nDiscount: {percent}%\nCode: {code}\nNew Amount: ₹{final}"
    )
    await show_payment(query, plan_id)


async def payment_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    track_user(update.effective_user)
    if not await ensure_age_access(update):
        return
    payment = context.user_data.get("payment")
    if not payment:
        return
    step = payment["step"]
    if step == "screenshot":
        if not update.message.photo:
            await update.message.reply_text("Please send a payment screenshot photo.")
            return
        payment["screenshot_file_id"] = update.message.photo[-1].file_id
        payment["step"] = "utr"
        await update.message.reply_text("Now send UTR number.")
    elif step == "utr":
        payment["utr"] = update.message.text.strip()
        payment["step"] = "message"
        await update.message.reply_text("Now send your message.")
    elif step == "message":
        payment["message"] = update.message.text or ""
        await create_ticket(update, context, payment)
        context.user_data.pop("payment", None)


async def create_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, payment: dict[str, Any]) -> None:
    plan_id = payment["plan_id"]
    with db() as conn:
        plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        discount = conn.execute(
            "SELECT * FROM discounts WHERE user_id = ? AND plan_id = ? ORDER BY id DESC LIMIT 1",
            (update.effective_user.id, plan_id),
        ).fetchone()
        final = discount["final_amount"] if discount else plan["price"]
        cursor = conn.execute(
            """
            INSERT INTO payment_tickets(
                user_id, plan_id, original_amount, discount_code, discount_percent,
                final_amount, utr, message, screenshot_file_id, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                update.effective_user.id,
                plan_id,
                plan["price"],
                discount["code"] if discount else None,
                discount["percent"] if discount else 0,
                final,
                payment["utr"],
                payment["message"],
                payment["screenshot_file_id"],
                now_iso(),
            ),
        )
        ticket_id = cursor.lastrowid
    await update.message.reply_text("Payment submitted. Please wait for admin review.")
    admin_text_value = (
        f"Payment Review #{ticket_id}\n"
        f"User: {update.effective_user.full_name} @{update.effective_user.username or '-'}\n"
        f"User ID: {update.effective_user.id}\n"
        f"Selected Plan: {plan['name']}\n"
        f"Original Price: ₹{plan['price']}\n"
        f"Discount: {(discount['code'] + ' ' + str(discount['percent']) + '%') if discount else '-'}\n"
        f"Final Amount: ₹{final}\n"
        f"UTR: {payment['utr']}\n"
        f"Message: {payment['message']}"
    )
    buttons = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🟢 Approve", callback_data=f"approve:{ticket_id}"), InlineKeyboardButton("🔴 Reject", callback_data=f"reject:{ticket_id}")]]
    )
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_photo(admin_id, payment["screenshot_file_id"], caption=admin_text_value, reply_markup=buttons)
        except TelegramError:
            await context.bot.send_message(admin_id, admin_text_value, reply_markup=buttons)


async def review_payment(query: Any, data: str) -> None:
    action, ticket_id_raw = data.split(":")
    ticket_id = int(ticket_id_raw)
    with db() as conn:
        ticket = conn.execute("SELECT * FROM payment_tickets WHERE id = ?", (ticket_id,)).fetchone()
        if not ticket:
            await query.message.reply_text("Ticket not found.")
            return
        if ticket["status"] != "pending":
            await query.message.reply_text("Ticket already reviewed.")
            return
        status = "approved" if action == "approve" else "rejected"
        conn.execute("UPDATE payment_tickets SET status = ?, reviewed_at = ? WHERE id = ?", (status, now_iso(), ticket_id))
        if status == "approved":
            conn.execute(
                "INSERT INTO premium_users(user_id, plan_id, purchase_date, status) VALUES(?, ?, ?, 'active') "
                "ON CONFLICT(user_id) DO UPDATE SET plan_id = excluded.plan_id, purchase_date = excluded.purchase_date, status = 'active'",
                (ticket["user_id"], ticket["plan_id"], now_iso()),
            )
    await query.message.reply_text(f"Payment {status}.")
    if status == "approved":
        await query.get_bot().send_message(
            ticket["user_id"],
            "Premium access link generate karne ke liye /premium command bhejein.\n"
            "Limit: 1 minute me 1 link, aur 1 din me maximum 10 links.",
        )
        await query.get_bot().send_message(ticket["user_id"], "✅ Payment approved. You are now a ⭐ PREMIUM USER.")
    else:
        await query.get_bot().send_message(ticket["user_id"], "❌ Payment rejected. Please contact admin.")


def invite_link_limit_message(user_id: int) -> str:
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with db() as conn:
        last = conn.execute(
            "SELECT created_at FROM invite_link_logs WHERE user_id = ? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        today_count = conn.execute(
            "SELECT COUNT(*) c FROM invite_link_logs WHERE user_id = ? AND created_at >= ?",
            (user_id, day_start.isoformat(timespec="seconds")),
        ).fetchone()["c"]
    if last:
        last_at = datetime.fromisoformat(last["created_at"])
        wait_seconds = INVITE_LINK_MIN_SECONDS - int((now - last_at).total_seconds())
        if wait_seconds > 0:
            return f"Please wait {wait_seconds} seconds. Aap 1 minute me sirf 1 invite link generate kar sakte hain."
    if today_count >= INVITE_LINK_DAILY_LIMIT:
        return "Daily limit complete ho gayi hai. Aap 1 din me sirf 10 invite links generate kar sakte hain."
    return ""


def log_invite_link(user_id: int, invite_link: str) -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO invite_link_logs(user_id, invite_link, created_at) VALUES(?, ?, ?)",
            (user_id, invite_link, now_iso()),
        )


async def generate_access_link(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    channel_id = get_setting("premium_channel_id")
    if not channel_id:
        await query.message.reply_text("Premium channel is not configured.")
        return
    with db() as conn:
        premium_row = conn.execute("SELECT 1 FROM premium_users WHERE user_id = ? AND status = 'active'", (query.from_user.id,)).fetchone()
    if not premium_row:
        await query.message.reply_text("Premium access is only for premium users.")
        return
    limit_message = invite_link_limit_message(query.from_user.id)
    if limit_message:
        await query.message.reply_text(limit_message)
        return
    try:
        invite = await context.bot.create_chat_invite_link(
            channel_id,
            member_limit=1,
            expire_date=datetime.now(timezone.utc) + timedelta(seconds=10),
            creates_join_request=False,
        )
        log_invite_link(query.from_user.id, invite.invite_link)
        await query.message.reply_text(f"Your Premium Access Link\n(valid for 10 seconds)\n{invite.invite_link}")
    except TelegramError as exc:
        await query.message.reply_text(f"Could not create invite link: {exc}")


async def broadcast_button_choice(query: Any, context: ContextTypes.DEFAULT_TYPE, wants_button: bool) -> None:
    if not is_admin(query.from_user.id):
        await deny(Update(0, callback_query=query))
        return
    if wants_button:
        context.user_data["wizard"] = {"name": "broadcast_button", "step": "name"}
        await query.message.reply_text("Send button name.")
    else:
        await query.message.reply_text(
            "Ready to send?",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🟢 Confirm & Send", callback_data="bcconfirm"), InlineKeyboardButton("🔴 Cancel", callback_data="bccancel")]]
            ),
        )


async def send_broadcast(query: Any, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(query.from_user.id):
        await deny(Update(0, callback_query=query))
        return
    payload = context.user_data.get("broadcast")
    if not payload:
        await query.message.reply_text("No broadcast prepared.")
        return
    with db() as conn:
        users = [row["user_id"] for row in conn.execute("SELECT user_id FROM users").fetchall()]
    sent = failed = 0
    markup = None
    if payload.get("button_name") and payload.get("button_url"):
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(payload["button_name"], url=payload["button_url"])]])
    for user_id in users:
        try:
            if payload["type"] == "photo":
                await context.bot.send_photo(user_id, payload["file_id"], caption=payload.get("caption", ""), reply_markup=markup)
            else:
                await context.bot.send_message(user_id, payload["text"], reply_markup=markup)
            sent += 1
            await asyncio.sleep(0.04)
        except TelegramError:
            failed += 1
    with db() as conn:
        conn.execute("INSERT INTO broadcast_logs(sent, failed, total, created_at) VALUES(?, ?, ?, ?)", (sent, failed, len(users), now_iso()))
    context.user_data.pop("wizard", None)
    context.user_data.pop("broadcast", None)
    await query.message.reply_text(f"Broadcast complete.\nSent: {sent}\nFailed: {failed}\nTotal: {len(users)}")


async def setup_bot_commands(app: Application) -> None:
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Start menu open karein"),
            BotCommand("help", "Help aur guide dekhein"),
            BotCommand("premium", "Premium panel open karein"),
            BotCommand("status", "Apna account status dekhein"),
        ]
    )


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Create .env from .env.example.")
    asyncio.set_event_loop(asyncio.new_event_loop())
    init_db()
    app = Application.builder().token(BOT_TOKEN).post_init(setup_bot_commands).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("premium", premium))
    app.add_handler(CommandHandler("status", user_status))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO, payment_message), group=0)
    app.add_handler(MessageHandler(filters.PHOTO, wizard_photo), group=1)
    app.add_handler(MessageHandler(filters.Document.ALL, wizard_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, payment_message), group=0)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, admin_text), group=1)
    logger.info("Bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
