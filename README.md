# 🤖 cipher-telegram-trader

**Telegram to MT5 trading bot** — copies signals from Telegram channels using Cipher Gateway SDK.

[![Python](https://img.shields.io/badge/Python-3.12%2B-blue)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-26A5E4)](https://t.me/CipherBot)

### 📌 Features

| Feature | Description |
| :--- | :--- |
| 🔄 **Auto-copy signals** | Listens to any Telegram channel or group |
| 📊 **Risk management** | Position sizing based on balance, risk %, and SL distance |
| 👥 **Multi-user support** | Each user connects their own MT5 account |
| ⚡ **Fast execution** | Under 2 seconds via Cipher Gateway |
| 📈 **Manual trading** | `/trade BUY EURUSD 0.1` |
| 💰 **Balance & positions** | Check account status anytime |


### 🏗️ How It Works
```
Telegram Channel/Group
│
▼ (signal)
Your Telegram Bot (CipherTrader)
│
▼ (REST/WebSocket)
Cipher Gateway (gateway.cipherbridge.cloud)
│
▼ (WebSocket)
CipherBridge (CMB) + MT5 Terminal
│
▼
Your Broker Account
```

### 📋 Commands

| Command | Description |
| :--- | :--- |
| `/start` | Welcome message |
| `/connect` | Connect your MT5 account |
| `/settings` | View/change risk settings |
| `/balance` | Check account balance |
| `/positions` | View open trades |
| `/trade` | Manual trade entry |
| `/help` | Show help |

---

### 🚀 Quick Start

#### Prerequisites

- Python 3.12+
- A Telegram bot token (from [@BotFather](https://t.me/BotFather))
- An MT5 account (demo or real)

### Installation
**1. Clone the repository**
```
git clone https://github.com/kachebutuk/cipher-telegram-trader.git
cd cipher-telegram-trader
```

**2. Create virtual environment**
```
python3 -m venv venv
```

**3. Activate it**
```
source venv/bin/activate      # On Linux/Mac
```
```
# venv\Scripts\activate       # On Windows
```
**4. Install dependencies**
```
pip install -r requirements.txt
```

### Configuration
Configure .env file in the project root:

```env
cp .env.example .env
nano .env
```

Run the Bot

```bash
python bot.py
```

**You should see:**

```
Bot started. Press Ctrl+C to stop.
```

---

### 🔧 Signal Format

**The bot understands signals in this format:**

```
BUY EURUSD
Entry NOW
SL 1.08000
TP 1.09500
```

**Or with limit/stop orders:**

```
SELL LIMIT GBPUSD
Entry 1.25000
SL 1.25500
TP 1.24000
```

### 📁 Project Structure

```
cipher-telegram-trader/
├── bot.py              # Main bot code
├── requirements.txt    # Dependencies
├── .env.example               # Configuration (not committed)
├── .gitignore
├── LICENSE
└── README.md
```

### 📦 Dependencies

- python-telegram-bot==22.6
- cipher-gateway==1.0.0
- python-dotenv==1.0.1


### 🤝 Contributing

1. Fork the repository
2. Create a feature branch (git checkout -b feature/amazing-feature)
3. Commit changes (git commit -m 'Add amazing feature')
4. Push to branch (git push origin feature/amazing-feature)
5. Open a Pull Request

## 📄 License

Distributed under the MIT License. See LICENSE for more information.

**### ⚠️ Disclaimer**

Trading involves risk of loss. This bot is for educational purposes. Always test with a demo account first. Past performance does not guarantee future results.

📞 Support

- Issues: GitHub Issues

<div align="center">

⭐ Star this repo if it helps you trade smarter

</div>
