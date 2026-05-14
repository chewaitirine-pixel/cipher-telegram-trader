#!/usr/bin/env python3
"""
Trading Bot — Copies signals from Telegram channels to MT5 via the Tonpo Gateway.
Uses the official tonpo SDK (v1.0.6) for all MT5 operations.
"""

import os
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, Dict
from dotenv import load_dotenv

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from tonpo import (
    TonpoClient,
    TonpoConfig,
    AccountLoginFailedError,
    AccountTimeoutError,
    AuthenticationError,
    TonpoConnectionError,
    TonpoError,
)

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment variables
# ---------------------------------------------------------------------------
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TONPO_HOST = os.getenv("TONPO_HOST", "gateway.tonpo.io")
TONPO_PORT = int(os.getenv("TONPO_PORT", 443))
TONPO_USE_SSL = os.getenv("TONPO_USE_SSL", "true").lower() == "true"

# Authorized Telegram user IDs (comma-separated)
ADMIN_USER_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip()
]

# Default risk management settings
DEFAULT_RISK_PERCENT = float(os.getenv("DEFAULT_RISK_PERCENT", 1.0))
DEFAULT_MAX_LOT = float(os.getenv("DEFAULT_MAX_LOT", 0.01))

# ---------------------------------------------------------------------------
# Tonpo configuration
# ---------------------------------------------------------------------------
tonpo_config = TonpoConfig(
    host=TONPO_HOST,
    port=TONPO_PORT,
    use_ssl=TONPO_USE_SSL,
)

# ---------------------------------------------------------------------------
# In-memory storage (for simplicity — upgrade to a real database later)
# ---------------------------------------------------------------------------
# telegram_id -> {"tonpo_api_key", "tonpo_account_id", "risk_percent", "max_lot"}
user_data: Dict[int, dict] = {}


@dataclass
class TradeSignal:
    """Parsed trading signal from a Telegram message."""
    action: str          # BUY or SELL
    order_type: str      # MARKET, LIMIT, STOP
    symbol: str
    entry: Optional[float]  # None means "NOW" (market)
    sl: Optional[float]
    tp: Optional[float]


# ===========================================================================
# TELEGRAM COMMAND HANDLERS
# ===========================================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a welcome message and initialise default settings for the user."""
    user_id = update.effective_user.id

    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ You are not authorised to use this bot.")
        return

    # Initialise default settings if the user is new
    if user_id not in user_data:
        user_data[user_id] = {
            "tonpo_api_key": None,
            "tonpo_account_id": None,
            "risk_percent": DEFAULT_RISK_PERCENT,
            "max_lot": DEFAULT_MAX_LOT,
        }

    await update.message.reply_text(
        f"🤖 *Trading Bot Active*\n\n"
        f"Welcome {update.effective_user.first_name}!\n\n"
        f"📌 *Commands:*\n"
        f"/connect — Connect your MT5 account\n"
        f"/settings — View or change risk settings\n"
        f"/balance — Check account balance\n"
        f"/positions — View open trades\n"
        f"/trade — Manual trade entry\n"
        f"/help — Show this help\n\n"
        f"⚡ The bot automatically copies signals from channels you add it to.",
        parse_mode="Markdown"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help information."""
    await update.message.reply_text(
        "📖 *Help*\n\n"
        "*Signal Format:*\n"
        "`BUY EURUSD`\n"
        "`Entry NOW`\n"
        "`SL 1.08000`\n"
        "`TP 1.09500`\n\n"
        "*Manual Trade:*\n"
        "`/trade BUY EURUSD 0.1`\n\n"
        "*Risk Settings:*\n"
        "`/settings risk 1.0` (1% risk per trade)\n"
        "`/settings maxlot 0.05` (max 0.05 lots)\n\n"
        "*Add bot to your signal channel:*\n"
        "1. Add @YourBotName to the channel\n"
        "2. Make it admin (so it can see messages)\n"
        "3. Signals are copied automatically",
        parse_mode="Markdown"
    )


async def connect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the MT5 connection flow via the Tonpo Gateway."""
    user_id = update.effective_user.id

    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Not authorised.")
        return

    # Check if already connected
    if user_data.get(user_id, {}).get("tonpo_api_key"):
        await update.message.reply_text(
            "✅ You are already connected. Use /settings to change risk."
        )
        return

    # Ask for MT5 credentials
    await update.message.reply_text(
        "🔐 *Connect your MT5 account*\n\n"
        "Please send your credentials in this format:\n\n"
        "`LOGIN:PASSWORD:SERVER`\n\n"
        "Example: `12345678:MyPassword:ICMarkets-Demo`\n\n"
        "⚠️ Your credentials are sent directly to the Tonpo Gateway and encrypted. "
        "They are never stored by this bot.",
        parse_mode="Markdown"
    )

    # Set state to expect MT5 credentials in the next message
    context.user_data["awaiting_mt5_creds"] = True


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Main message handler.
    Routes messages to either MT5 credential processing or signal parsing.
    """
    user_id = update.effective_user.id

    if user_id not in ADMIN_USER_IDS:
        return

    # 1. Credential submission flow
    if context.user_data.get("awaiting_mt5_creds"):
        await process_mt5_credentials(update, context)
        return

    # 2. Signal parsing (only for channel/group messages)
    if update.message.chat.type in ["channel", "group", "supergroup"]:
        await process_signal(update, context)


# ===========================================================================
# MT5 ACCOUNT SETUP
# ===========================================================================

async def process_mt5_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Validate and use MT5 credentials submitted by the user.
    Creates a Tonpo user, connects an MT5 account, and waits for it to go active.
    """
    user_id = update.effective_user.id
    text = update.message.text.strip()

    # Parse LOGIN:PASSWORD:SERVER
    parts = text.split(":")
    if len(parts) != 3:
        await update.message.reply_text(
            "❌ Invalid format. Please use:\n`LOGIN:PASSWORD:SERVER`\n\n"
            "Example: `12345678:MyPassword:ICMarkets-Demo`",
            parse_mode="Markdown"
        )
        return

    mt5_login, mt5_password, mt5_server = parts

    status_message = await update.message.reply_text(
        "⏳ Connecting to Tonpo Gateway and provisioning MT5… (this may take up to 2 minutes)"
    )

    try:
        # --- Step 1: Create a Tonpo user (admin client, no auth needed) ---
        async with TonpoClient.admin(tonpo_config) as admin_client:
            user_credentials = await admin_client.create_user()
            logger.info(f"Tonpo user created: {user_credentials.gateway_user_id}")

        # --- Step 2: Register an MT5 account for this user ---
        async with TonpoClient.for_user(tonpo_config, user_credentials.api_key) as user_client:
            account = await user_client.create_account(
                mt5_login=mt5_login,
                mt5_password=mt5_password,
                mt5_server=mt5_server,
            )
            logger.info(f"MT5 account created: {account.account_id}")

            # --- Step 3: Wait for MT5 to log in and become active ---
            await status_message.edit_text(
                "⏳ Waiting for MT5 to connect to broker… (30–180 seconds)"
            )

            try:
                await user_client.wait_for_active(account.account_id, timeout=180)
            except AccountLoginFailedError:
                # Credentials were rejected by the broker — clean up the stale account
                await user_client.delete_account(account.account_id)
                await status_message.edit_text(
                    "❌ MT5 login failed. The broker rejected your credentials. "
                    "Please check your login, password, and server and try again."
                )
                return
            except AccountTimeoutError:
                # MT5 did not connect within the timeout — clean up
                await user_client.delete_account(account.account_id)
                await status_message.edit_text(
                    "❌ MT5 connection timed out. The broker may be slow or your "
                    "credentials may be incorrect. Please try again later."
                )
                return

        # --- Step 4: Store the secure tokens — MT5 credentials are never saved ---
        user_data[user_id] = {
            "tonpo_api_key": user_credentials.api_key,
            "tonpo_account_id": account.account_id,
            "risk_percent": DEFAULT_RISK_PERCENT,
            "max_lot": DEFAULT_MAX_LOT,
        }

        await status_message.edit_text(
            f"✅ *MT5 Connected Successfully!*\n\n"
            f"Account: {mt5_login} @ {mt5_server}\n"
            f"Default risk: {DEFAULT_RISK_PERCENT}%\n"
            f"Default max lot: {DEFAULT_MAX_LOT}\n\n"
            f"Add this bot to your signal channel — trades will copy automatically.",
            parse_mode="Markdown"
        )

    except TonpoConnectionError as e:
        logger.error(f"Connection error during MT5 setup: {e}")
        await status_message.edit_text(
            "❌ Cannot reach the Tonpo Gateway. Please check your internet connection "
            "and try again later."
        )
    except TonpoError as e:
        logger.error(f"Tonpo error during MT5 setup: {e}")
        await status_message.edit_text(
            f"❌ Tonpo Gateway error: {e}\n\nPlease try again later or contact support."
        )
    finally:
        # Clear the "expecting credentials" state
        context.user_data["awaiting_mt5_creds"] = False


# ===========================================================================
# SIGNAL PARSING & TRADE EXECUTION
# ===========================================================================

def parse_signal(text: str) -> Optional[TradeSignal]:
    """
    Parse a Telegram signal message into a structured TradeSignal.
    Expected format:
        BUY EURUSD
        Entry NOW
        SL 1.08000
        TP 1.09500
    """
    lines = [line.strip() for line in text.strip().split("\n") if line.strip()]

    if len(lines) < 3:
        return None

    # First line: BUY/SELL [LIMIT/STOP] SYMBOL
    first_parts = lines[0].split()
    if len(first_parts) < 2:
        return None

    action = first_parts[0].upper()
    if action not in ["BUY", "SELL"]:
        return None

    # Check for order type
    order_type = "MARKET"
    symbol_index = 1
    if len(first_parts) > 2 and first_parts[1].upper() in ["LIMIT", "STOP"]:
        order_type = first_parts[1].upper()
        symbol_index = 2

    symbol = first_parts[symbol_index].upper()

    # Parse entry, SL, TP
    entry = None
    sl = None
    tp = None

    for line in lines[1:]:
        upper_line = line.upper()
        if upper_line.startswith("ENTRY"):
            entry_part = line.replace("Entry", "", 1).replace("entry", "", 1).strip()
            if entry_part.upper() == "NOW":
                entry = None
            else:
                try:
                    entry = float(entry_part)
                except ValueError:
                    pass
        elif upper_line.startswith("SL"):
            try:
                sl = float(line.replace("SL", "", 1).replace("sl", "", 1).strip())
            except ValueError:
                pass
        elif upper_line.startswith("TP"):
            try:
                tp = float(line.replace("TP", "", 1).replace("tp", "", 1).strip())
            except ValueError:
                pass

    return TradeSignal(
        action=action,
        order_type=order_type,
        symbol=symbol,
        entry=entry,
        sl=sl,
        tp=tp,
    )


async def process_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Parse a signal message and execute the trade via Tonpo."""
    user_id = update.effective_user.id

    # Only process authorised users
    if user_id not in ADMIN_USER_IDS:
        return

    # Check that the user has connected MT5
    record = user_data.get(user_id)
    if not record or not record.get("tonpo_api_key"):
        logger.warning(f"User {user_id} sent a signal but has no connected MT5 account.")
        return

    message_text = update.message.text
    if not message_text:
        return

    signal = parse_signal(message_text)
    if not signal:
        logger.info(f"Could not parse signal from message: {message_text}")
        return

    api_key = record["tonpo_api_key"]
    risk_percent = record["risk_percent"]
    max_lot = record["max_lot"]

    # Calculate position size (fall back to max_lot if calculation fails)
    volume = max_lot
    if signal.entry and signal.sl:
        try:
            volume = await calculate_position_size(api_key, signal.symbol, risk_percent, signal.entry, signal.sl)
            volume = min(volume, max_lot)
        except Exception as e:
            logger.warning(f"Position size calculation failed, using max_lot: {e}")

    # Dispatch the correct order type
    try:
        async with TonpoClient.for_user(tonpo_config, api_key) as client:
            if signal.order_type == "MARKET":
                result = await execute_market_order(client, signal, volume)
            elif signal.order_type == "LIMIT":
                result = await execute_limit_order(client, signal, volume)
            elif signal.order_type == "STOP":
                result = await execute_stop_order(client, signal, volume)
            else:
                await update.message.reply_text(f"❌ Unknown order type: {signal.order_type}")
                return

            if result.success:
                await update.message.reply_text(
                    f"✅ *Trade Executed*\n"
                    f"{signal.action} {signal.symbol}\n"
                    f"Volume: {volume}\n"
                    f"SL: {signal.sl}\n"
                    f"TP: {signal.tp}\n"
                    f"Ticket: {result.ticket}",
                    parse_mode="Markdown"
                )
                logger.info(f"Executed {signal.action} {signal.symbol} volume={volume} ticket={result.ticket}")
            else:
                await update.message.reply_text(
                    f"❌ Trade failed: {result.error}\n\n"
                    f"Action: {signal.action} {signal.symbol}\n"
                    f"Volume: {volume}"
                )
                logger.error(f"Trade failed for {signal.action} {signal.symbol}: {result.error}")

    except AuthenticationError:
        await update.message.reply_text(
            "❌ Tonpo authentication failed. Your API key may have expired. "
            "Please use /connect to reconnect your MT5 account."
        )
    except TonpoConnectionError:
        await update.message.reply_text(
            "❌ Cannot reach the Tonpo Gateway. Please check your internet connection."
        )
    except TonpoError as e:
        logger.error(f"Tonpo error during trade execution: {e}")
        await update.message.reply_text(f"❌ Tonpo error: {e}")
    except Exception as e:
        logger.exception(f"Unexpected error during trade execution: {e}")
        await update.message.reply_text(f"❌ Unexpected error: {e}")


async def execute_market_order(client: TonpoClient, signal: TradeSignal, volume: float):
    """Place a market order via the Tonpo client."""
    if signal.action == "BUY":
        return await client.place_market_buy(
            symbol=signal.symbol,
            volume=volume,
            sl=signal.sl,
            tp=signal.tp,
        )
    else:
        return await client.place_market_sell(
            symbol=signal.symbol,
            volume=volume,
            sl=signal.sl,
            tp=signal.tp,
        )


async def execute_limit_order(client: TonpoClient, signal: TradeSignal, volume: float):
    """Place a limit order via the Tonpo client."""
    if signal.entry is None:
        raise ValueError("Limit order requires an entry price.")

    if signal.action == "BUY":
        return await client.place_limit_buy(
            symbol=signal.symbol,
            volume=volume,
            price=signal.entry,
            sl=signal.sl,
            tp=signal.tp,
        )
    else:
        return await client.place_limit_sell(
            symbol=signal.symbol,
            volume=volume,
            price=signal.entry,
            sl=signal.sl,
            tp=signal.tp,
        )


async def execute_stop_order(client: TonpoClient, signal: TradeSignal, volume: float):
    """Place a stop order via the Tonpo client."""
    if signal.entry is None:
        raise ValueError("Stop order requires an entry price.")

    if signal.action == "BUY":
        return await client.place_stop_buy(
            symbol=signal.symbol,
            volume=volume,
            price=signal.entry,
            sl=signal.sl,
            tp=signal.tp,
        )
    else:
        return await client.place_stop_sell(
            symbol=signal.symbol,
            volume=volume,
            price=signal.entry,
            sl=signal.sl,
            tp=signal.tp,
        )


async def calculate_position_size(
    api_key: str,
    symbol: str,
    risk_percent: float,
    entry: float,
    sl: float,
) -> float:
    """
    Calculate lot size based on account balance, risk percentage, and stop-loss distance.
    This is a simplified calculation that approximates pip value.
    """
    try:
        async with TonpoClient.for_user(tonpo_config, api_key) as client:
            info = await client.get_account_info()
            balance = info.balance

        # Risk amount in account currency
        risk_amount = balance * (risk_percent / 100)

        # SL distance in absolute price units
        sl_distance = abs(entry - sl)

        # Approximate pip value (simplified — works best on XXXUSD pairs)
        # For 0.1 lot on EURUSD, 1 pip ≈ 1.0
        # This scales linearly with volume
        pip_value_per_01_lot = 1.0
        lot_size = risk_amount / (sl_distance * pip_value_per_01_lot * 10)

        # Round to 2 decimal places and enforce limits
        lot_size = round(lot_size, 2)
        lot_size = max(0.01, min(lot_size, 1.0))

        return lot_size

    except Exception as e:
        logger.error(f"Failed to calculate position size: {e}")
        return 0.01  # safe fallback


# ===========================================================================
# ACCOUNT & POSITION COMMANDS
# ===========================================================================

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the connected MT5 account balance."""
    user_id = update.effective_user.id

    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Not authorised.")
        return

    api_key = user_data.get(user_id, {}).get("tonpo_api_key")
    if not api_key:
        await update.message.reply_text("❌ MT5 not connected. Use /connect first.")
        return

    try:
        async with TonpoClient.for_user(tonpo_config, api_key) as client:
            info = await client.get_account_info()
            await update.message.reply_text(
                f"💰 *Account Balance*\n\n"
                f"Balance: {info.balance:.2f} {info.currency}\n"
                f"Equity: {info.equity:.2f} {info.currency}\n"
                f"Free Margin: {info.free_margin:.2f} {info.currency}\n"
                f"Profit: {info.profit:.2f} {info.currency}\n"
                f"Margin Level: {info.margin_level:.2f}%",
                parse_mode="Markdown"
            )
    except TonpoConnectionError:
        await update.message.reply_text("❌ Cannot reach the Tonpo Gateway.")
    except TonpoError as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def positions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show open positions."""
    user_id = update.effective_user.id

    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Not authorised.")
        return

    api_key = user_data.get(user_id, {}).get("tonpo_api_key")
    if not api_key:
        await update.message.reply_text("❌ MT5 not connected. Use /connect first.")
        return

    try:
        async with TonpoClient.for_user(tonpo_config, api_key) as client:
            positions_list = await client.get_positions()
            if not positions_list:
                await update.message.reply_text("📭 No open positions.")
                return

            msg = "*Open Positions*\n\n"
            for p in positions_list[:10]:  # Limit to 10 to avoid message length issues
                msg += (
                    f"🔹 {p.symbol} {p.side} {p.volume} lot(s)\n"
                    f"   Open: {p.open_price} | Current: {p.current_price}\n"
                    f"   Profit: {p.profit:.2f} | Ticket: {p.ticket}\n\n"
                )

            await update.message.reply_text(msg, parse_mode="Markdown")
    except TonpoConnectionError:
        await update.message.reply_text("❌ Cannot reach the Tonpo Gateway.")
    except TonpoError as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def trade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Execute a manual trade via command: /trade BUY EURUSD 0.1 [SL] [TP]"""
    user_id = update.effective_user.id

    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Not authorised.")
        return

    api_key = user_data.get(user_id, {}).get("tonpo_api_key")
    if not api_key:
        await update.message.reply_text("❌ MT5 not connected. Use /connect first.")
        return

    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ Usage: `/trade BUY EURUSD 0.1`\n"
            "Optional: `/trade BUY EURUSD 0.1 1.0800 1.0950` (SL and TP)",
            parse_mode="Markdown"
        )
        return

    action = args[0].upper()
    symbol = args[1].upper()

    try:
        volume = float(args[2])
    except ValueError:
        await update.message.reply_text("❌ Volume must be a number (e.g., 0.1)")
        return

    sl = float(args[3]) if len(args) >= 4 else None
    tp = float(args[4]) if len(args) >= 5 else None

    if action not in ["BUY", "SELL"]:
        await update.message.reply_text("❌ Action must be BUY or SELL")
        return

    try:
        async with TonpoClient.for_user(tonpo_config, api_key) as client:
            if action == "BUY":
                result = await client.place_market_buy(symbol=symbol, volume=volume, sl=sl, tp=tp)
            else:
                result = await client.place_market_sell(symbol=symbol, volume=volume, sl=sl, tp=tp)

            if result.success:
                await update.message.reply_text(
                    f"✅ {action} {symbol} {volume} lots executed.\nTicket: {result.ticket}"
                )
            else:
                await update.message.reply_text(f"❌ Trade failed: {result.error}")
    except TonpoConnectionError:
        await update.message.reply_text("❌ Cannot reach the Tonpo Gateway.")
    except TonpoError as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View or change user risk settings."""
    user_id = update.effective_user.id

    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Not authorised.")
        return

    # Ensure user has a settings record
    if user_id not in user_data:
        user_data[user_id] = {
            "tonpo_api_key": None,
            "tonpo_account_id": None,
            "risk_percent": DEFAULT_RISK_PERCENT,
            "max_lot": DEFAULT_MAX_LOT,
        }

    args = context.args

    if not args:
        # Show current settings
        settings = user_data[user_id]
        await update.message.reply_text(
            f"⚙️ *Your Settings*\n\n"
            f"Risk %: {settings['risk_percent']}%\n"
            f"Max Lot: {settings['max_lot']}\n\n"
            f"To change:\n"
            f"`/settings risk 1.0`\n"
            f"`/settings maxlot 0.05`",
            parse_mode="Markdown"
        )
        return

    if len(args) >= 2:
        setting = args[0].lower()
        value_str = args[1]

        if setting == "risk":
            try:
                new_risk = float(value_str)
                if 0.1 <= new_risk <= 10:
                    user_data[user_id]["risk_percent"] = new_risk
                    await update.message.reply_text(f"✅ Risk set to {new_risk}%")
                else:
                    await update.message.reply_text("❌ Risk must be between 0.1% and 10%")
            except ValueError:
                await update.message.reply_text("❌ Invalid number")

        elif setting == "maxlot":
            try:
                new_maxlot = float(value_str)
                if 0.01 <= new_maxlot <= 10:
                    user_data[user_id]["max_lot"] = new_maxlot
                    await update.message.reply_text(f"✅ Max lot set to {new_maxlot}")
                else:
                    await update.message.reply_text("❌ Max lot must be between 0.01 and 10")
            except ValueError:
                await update.message.reply_text("❌ Invalid number")

        else:
            await update.message.reply_text("❌ Unknown setting. Use `risk` or `maxlot`")
    else:
        await update.message.reply_text("❌ Usage: `/settings risk 1.0` or `/settings maxlot 0.05`")


# ===========================================================================
# MAIN ENTRY POINT
# ===========================================================================

def main():
    """Start the Telegram bot."""
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env file")
        return

    # Build the application
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("connect", connect))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("positions", positions))
    app.add_handler(CommandHandler("trade", trade))
    app.add_handler(CommandHandler("settings", settings_command))

    # Message handler for non-command text (credentials & signals)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
