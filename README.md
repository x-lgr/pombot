```markdown
# 📱 Telegram Premium Bot - Complete Documentation

<div align="center">

![Telegram Bot](https://img.shields.io/badge/Telegram-Bot-0088cc?style=for-the-badge&logo=telegram&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.9+-3776ab?style=for-the-badge&logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-Database-003b57?style=for-the-badge&logo=sqlite&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)

### 🚀 Feature-Rich Telegram Bot with Premium Membership System

[Features](#-features) • [Setup](#-setup--installation) • [Commands](#-commands) • [Configuration](#-configuration)

</div>

---

## 📖 Overview

A complete Telegram bot solution with premium membership management, payment integration, broadcast system, and much more. Perfect for content creators, channel owners, and businesses looking to monetize their Telegram presence.

### ✨ Key Features

| Category | Features |
|----------|----------|
| **💎 Premium System** | Multi-plan subscriptions, Discount system (1-25% random), Secure invite links (10s expiry), Premium user panel |
| **🎨 Start System** | Customizable start message with HTML/Markdown, Custom start image support, Dynamic inline buttons |
| **💰 Payment Integration** | UPI payments, Auto payment URL generation, Payment verification workflow, Screenshot & UTR submission |
| **📢 Broadcast System** | Message with buttons support, Photo + caption broadcast, Send to all users, Delivery statistics |
| **👥 User Management** | Auto user backup (users.txt), Database rebuild from backup, User statistics, Premium status tracking |
| **🔧 Admin Controls** | ?? prefix for admin commands, Full configuration panel, Plan management, Button management |

---

## 🚀 Setup & Installation

### Prerequisites

- Python 3.9 or higher
- Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Server/VPS (Recommended) or local machine

### Step 1: Clone Repository

```bash
git clone https://github.com/x-lgr/pombot.git
cd pombot
```

### Step 2: Create Virtual Environment

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# Linux/Mac
python3 -m venv venv
source venv/bin/activate
```

### Step 3: Install Dependencies

```bash
pip install python-telegram-bot python-dotenv qrcode Pillow
```

### Step 4: Configure Environment Variables

Create a `.env` file in the root directory:

```env
# Required: Your bot token from @BotFather
BOT_TOKEN=1234567890:ABCdefGHIjklmNOPqrsTUVwxyz

# Required: Admin user IDs (comma-separated, no spaces)
ADMIN_IDS=123456789,987654321

# Optional: Custom data directory
BOT_DATA_DIR=./data
```

### Step 5: Run the Bot

```bash
python bot.py
```

### Step 6: Setup Bot Commands (Optional)

Send these commands to [@BotFather](https://t.me/BotFather):

```
/setcommands
start - Start the bot
help - Show help menu
premium - Premium user panel
status - Check account status
```

---

## 📝 Commands

### 👤 User Commands

| Command | Description |
|---------|-------------|
| `/start` | Launch bot and show start message with age verification |
| `/help` | Display help menu with available commands |
| `/premium` | Access premium user panel (premium users only) |
| `/status` | Check your account status (free/premium) |

### 👑 Admin Commands (Prefix `??`)

#### Start Message Management
| Command | Description |
|---------|-------------|
| `??setstart` | Set custom start message (supports HTML/placeholders) |
| `??viewstart` | View current start message |
| `??resetstart` | Restore default start message |

#### Start Image Management
| Command | Description |
|---------|-------------|
| `??setstartimage` | Set custom start image (send photo after command) |
| `??viewstartimage` | Preview current start image |
| `??removestartimage` | Remove current start image |
| `??resetstartimage` | Restore default image |

#### Button Management
| Command | Description |
|---------|-------------|
| `??addbutton` | Add new button (asks name & URL) |
| `??removebutton` | Remove existing button |
| `??editbutton` | Edit button name or URL |
| `??buttons` | List all configured buttons |
| `??setbuybuttonname` | Change Buy Premium button text |
| `??setdemobuttonname` | Change Demo button text |
| `??setdemourl` | Set Demo button URL |

#### Plan Management
| Command | Description |
|---------|-------------|
| `??addplan` | Create new premium plan |
| `??editplan` | Edit existing plan |
| `??removeplan` | Delete a plan |
| `??plans` | List all plans |

#### Payment Configuration
| Command | Description |
|---------|-------------|
| `??setupi` | Set UPI ID (e.g., example@upi) |
| `??setupiname` | Set UPI name for payment URL |
| `??setreceivername` | Set receiver name |
| `??setbankname` | Set bank name for payment URL |
| `??setpaymenturl` | Set custom payment URL template |

#### Channel Settings
| Command | Description |
|---------|-------------|
| `??setpremiumchannel` | Set premium channel (bot must be admin) |
| `??setdemochannel` | Set demo channel URL |

#### Broadcast & Users
| Command | Description |
|---------|-------------|
| `??broadcast` | Send broadcast message/photo to all users |
| `??backupusers` | Download users.txt backup file |
| `??importusers` | Import users from uploaded file |
| `??rebuilddb` | Rebuild database from users.txt |

#### Status & Statistics
| Command | Description |
|---------|-------------|
| `??status` | Show bot system status |
| `??stats` | Show advanced statistics |
| `??help` | Show all admin commands |

---

## ⚙️ Configuration Guide

### 1. Setting Up Start Message

```bash
??setstart
```

Then send your message. Supports:
- HTML formatting: `<b>Bold</b>`, `<i>Italic</i>`
- Placeholders: `{first_name}`, `{last_name}`, `{username}`, `{user_id}`

Example:
```
Welcome {first_name}! 👋
Your ID: {user_id}
<b>Enjoy premium content!</b>
```

### 2. Adding Start Buttons

```bash
??addbutton
```

Follow prompts to add:
- Button name (e.g., "Join Channel")
- Button URL (e.g., "https://t.me/mychannel")

### 3. Creating Premium Plans

```bash
??addplan
```

Provide:
- Plan name (e.g., "🔥 Premium Pro")
- Price in rupees (e.g., 199)
- Description (e.g., "Access all features")
- Duration (e.g., "30 days" or "-" to skip)
- Sort order (1 = first position)

### 4. Configuring Payment System

```bash
# Set UPI details
??setupi example@okhdfcbank
??setupiname merchantname
??setreceivername "John Doe"
??setbankname bankname
```

The payment URL will be generated as:
`https://redirect-beta-lemon.vercel.app/merchantname/bankname/amount`

### 5. Setting Up Premium Channel

1. Add bot as admin to your premium channel
2. Get channel ID:
   - Forward a message from channel to @userinfobot
   - Or use: `-100` + channel ID
3. Set it:
```bash
??setpremiumchannel -1001234567890
```

### 6. Demo Channel Configuration

```bash
??setdemochannel https://t.me/demochannel
```

---

## 🗄️ Database Structure

### Tables

| Table | Purpose |
|-------|---------|
| `users` | Store user information and verification status |
| `settings` | Bot configuration (start message, button names, payment settings) |
| `start_buttons` | Start menu inline buttons |
| `plans` | Premium subscription plans |
| `discounts` | Generated discount codes per user/plan |
| `payment_tickets` | Payment verification queue with screenshot & UTR |
| `premium_users` | Active premium subscriptions |
| `invite_link_logs` | Rate-limiting for invite links |
| `broadcast_logs` | Broadcast delivery statistics |

### Backup System

The bot maintains `users.txt` with one user ID per line:
```
123456789
987654321
555555555
```

To restore from backup:
```bash
??importusers  # Upload users.txt
??rebuilddb    # Rebuild database
```

---

## 🎯 Usage Examples

### User Flow

1. **User starts bot** → Age verification → Welcome message with buttons
2. **Clicks "Buy Premium"** → Sees available plans
3. **Selects a plan** → Views plan details
4. **Clicks "Buy Now"** → Gets payment instructions with QR code
5. **Cancels payment** → Gets discount offer (1-25% random)
6. **Makes payment** → Submits screenshot & UTR
7. **Admin approves** → User gets premium access
8. **Premium user** → Uses `/premium` to generate invite link (valid 10 seconds)

### Admin Workflow

1. **Setup bot** → Configure start message, image, buttons
2. **Create plans** → Add pricing plans with `??addplan`
3. **Configure payment** → Set UPI details
4. **Set channels** → Premium & demo channel config
5. **Review payments** → Approve/reject user submissions via inline buttons
6. **Send broadcasts** → Announcements to all users with `??broadcast`
7. **Manage users** → Backup, import, view statistics

---

## 🔒 Security Features

- **Admin-only commands** with `??` prefix and ID verification
- **Age verification** (18+) before bot access
- **Secure invite links** (10-second expiry, one-time use)
- **Rate limiting** for invite links (1 per minute, 10 per day)
- **SQL injection prevention** with parameterized queries
- **Input validation** for URLs and user data
- **Automatic database backups**

---

## 📁 Project Structure

```
pombot/
├── bot.py                 # Main bot application
├── .env                   # Environment variables
├── requirements.txt       # Dependencies
├── data/                  # Data directory (auto-created)
│   ├── premium_bot.sqlite # SQLite database
│   ├── users.txt          # User backup file
│   └── users.txt.bak      # Automatic backup
└── README.md              # Documentation
```

---

## 🛠️ Troubleshooting

### Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| Bot not responding | Check bot token in `.env` file |
| Admin commands not working | Verify user ID in `ADMIN_IDS` |
| Payment URL not generating | Set `upiname` and `bankname` via commands |
| Invite link fails | Ensure bot is admin in premium channel |
| Users not saving | Check `data/` directory permissions |
| Broadcast fails | Bot may be blocked by some users (normal) |
| Database locked | Bot uses `journal_mode=OFF` for shared hosting |

### Getting Channel ID

1. Add bot to channel as admin
2. Forward any message from channel to @userinfobot
3. Copy the channel ID (starts with -100)

### Logs

Check console output for detailed error messages:
```
2024-01-01 12:00:00 - INFO - Bot started!
2024-01-01 12:00:05 - ERROR - Failed to send message to user
```

---

## 🔄 Deployment

### Using systemd (Linux)

Create service file:
```bash
sudo nano /etc/systemd/system/pombot.service
```

Add:
```ini
[Unit]
Description=POMBot Telegram Bot
After=network.target

[Service]
Type=simple
User=youruser
WorkingDirectory=/home/youruser/pombot
ExecStart=/home/youruser/pombot/venv/bin/python bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Start service:
```bash
sudo systemctl daemon-reload
sudo systemctl start pombot
sudo systemctl enable pombot
```

### Using screen

```bash
screen -S pombot
python bot.py
# Press Ctrl+A, then D to detach
# screen -r pombot to reattach
```

### Using Docker

Create `Dockerfile`:
```dockerfile
FROM python:3.9-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

CMD ["python", "bot.py"]
```

Build and run:
```bash
docker build -t pombot .
docker run -d --name pombot pombot
```

---

## 📊 Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `BOT_TOKEN` | ✅ Yes | None | Bot token from @BotFather |
| `ADMIN_IDS` | ✅ Yes | None | Comma-separated admin user IDs |
| `BOT_DATA_DIR` | ❌ No | `./data` | Custom data directory path |

---

## 📄 License

MIT License

Copyright (c) 2024 x-lgr

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/x-lgr/pombot/issues)
- **Telegram**: [@xlgr_158](https://t.me/xlgr_158)

---

## ⭐ Show Your Support

If this project helped you, please give it a ⭐ on GitHub!

<div align="center">

Made with ❤️ by [x-lgr]

[Report Bug](https://github.com/x-lgr/pombot/issues) • [Request Feature](https://github.com/x-lgr/pombot/issues)

</div>
```
