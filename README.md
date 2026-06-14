# Premium Sales Telegram Bot

Python Telegram bot with configurable start message/image/buttons, premium plan sales, UPI payment review, broadcasts, stats, and user backups.

## Setup

1. Install dependencies:

```powershell
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and set:

```env
BOT_TOKEN=your_bot_token
ADMIN_IDS=123456789,987654321
```

3. Run:

```powershell
python bot.py
```

The bot stores persistent data in `data/premium_bot.sqlite` and keeps user ID backup data in `data/users.txt`.
Set `BOT_DATA_DIR` if you want those files somewhere else.

## Command Rules

User commands use `/`, for example `/start`, `/help`, `/premium`, `/status`.

Admin commands use `??`, for example `??setstart`, `??addplan`, `??broadcast`, `??stats`.
Slash variants of admin commands are intentionally not supported.
