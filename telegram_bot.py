#!/usr/bin/env python3
"""
Telegram Bot for Medical Portal Integration

This bot provides a Telegram interface to the medical portal, allowing users to:
- View messages from their medical file
- Ask questions to their doctor
- Receive automatic notifications for new messages

Features:
- Two concurrent workers: command handler and message checker
- Interactive inline keyboards for message navigation
- Session persistence with automatic re-authentication
- 2FA support via Telegram chat
"""

import asyncio
import json
import logging
import os
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from concurrent.futures import ThreadPoolExecutor

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, filters, ContextTypes
)
from telegram.error import TelegramError

from medical_portal_client import MedicalPortalClient, TwoFactorAuthData
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Disable httpx logs
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# Conversation states for 2FA
WAITING_FOR_SMS_CODE = 1

class TelegramMedicalBot:
    """Main bot class handling Telegram integration with medical portal."""
    
    def __init__(self):
        """Initialize the bot with configuration from environment variables."""
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.medical_email = os.getenv('MEDICAL_PORTAL_EMAIL')
        self.medical_password = os.getenv('MEDICAL_PORTAL_PASSWORD')
        self.check_interval = int(os.getenv('CHECK_INTERVAL', '120'))  # 2 minutes default
        
        if not all([self.bot_token, self.chat_id, self.medical_email, self.medical_password]):
            raise ValueError("Missing required environment variables. Check .env file.")
        
        # Convert chat_id to string for consistent comparison
        self.authorized_chat_id = str(self.chat_id)
        
        # Initialize medical portal client
        self.medical_client = MedicalPortalClient()
        self.executor = ThreadPoolExecutor(max_workers=2)
        
        # State management
        self.last_message_state_file = '/app/data/last_message_state.json'
        self.is_authenticated = False
        self.message_checker_running = False
        
        # Load last message state
        self.last_message_id = self._load_last_message_state()
        
        # Initialize Telegram application
        self.application = Application.builder().token(self.bot_token).build()
        self._setup_handlers()
    
    def _load_last_message_state(self) -> Optional[int]:
        """Load the last processed message ID from file."""
        try:
            if os.path.exists(self.last_message_state_file):
                with open(self.last_message_state_file, 'r') as f:
                    data = json.load(f)
                    return data.get('last_message_id')
        except Exception as e:
            logger.warning(f"Failed to load last message state: {e}")
        return None
    
    def _save_last_message_state(self, message_id: int) -> None:
        """Save the last processed message ID to file."""
        try:
            data = {
                'last_message_id': message_id,
                'last_updated': datetime.now().isoformat()
            }
            with open(self.last_message_state_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save last message state: {e}")
    
    def _is_authorized_chat(self, update: Update) -> bool:
        """Check if the message is from an authorized chat."""
        chat_id = str(update.effective_chat.id)
        is_authorized = chat_id == self.authorized_chat_id
        
        if not is_authorized:
            logger.warning(f"Unauthorized access attempt from chat ID: {chat_id} (authorized: {self.authorized_chat_id})")
        
        return is_authorized
    
    def _setup_handlers(self) -> None:
        """Setup all command and callback handlers."""
        # Conversation handler for 2FA - add more fallbacks to prevent blocking
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("auth", self.start_auth)],
            states={
                WAITING_FOR_SMS_CODE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_sms_code),
                    CommandHandler("cancel", self.cancel_auth),
                    CommandHandler("help", self.help_command),
                    CommandHandler("start", self.start_command)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.cancel_auth),
                CommandHandler("help", self.help_command),
                CommandHandler("start", self.start_command)
            ],
            per_message=False,  # Allow other handlers to process messages
            per_chat=True,      # Conversation state per chat
            per_user=True       # Conversation state per user
        )
        
        # Command handlers - add them in order of priority
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("messages", self.messages_command))
        self.application.add_handler(CommandHandler("ask", self.ask_command))
        self.application.add_handler(conv_handler)  # Add conversation handler last
        
        # Callback query handler for inline buttons
        self.application.add_handler(CallbackQueryHandler(self.handle_callback_query))
        
        # Error handler
        self.application.add_error_handler(self.error_handler)
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command."""
        if not self._is_authorized_chat(update):
            return
        
        welcome_text = """
🏥 *Medical Portal Bot*

Welcome! This bot helps you interact with your medical portal.

*Available Commands:*
• /messages - View your medical messages
• /ask <question> - Ask a question to your doctor
• /auth - Re-authenticate if needed
• /help - Show this help message

*Automatic Features:*
• New message notifications
• Session persistence
• 2FA support

The bot will automatically check for new messages every 5 minutes and notify you when they arrive.
        """
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /help command."""
        if not self._is_authorized_chat(update):
            return
        
        help_text = """
*Medical Portal Bot Commands:*

🔹 /messages - List your medical messages with interactive buttons
🔹 /ask <your question> - Send a question to your doctor
🔹 /auth - Re-authenticate with the medical portal
🔹 /help - Show this help message

*How to use:*
1. Use `/messages` to see your messages
2. Click the buttons to view message details
3. Use `/ask` to send questions to your doctor
4. The bot automatically notifies you of new messages

*Note:* The bot checks for new messages every 5 minutes automatically.
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')
    
    async def messages_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /messages command - show inbox messages with inline buttons."""
        if not self._is_authorized_chat(update):
            return
        
        await update.message.reply_text("📋 Fetching your messages...")
        
        try:
            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            messages = await loop.run_in_executor(
                self.executor, self._get_messages_sync
            )
            
            if messages is None:
                # Authentication required but not available
                await update.message.reply_text(
                    "🔐 Authentication required. Please use /auth to authenticate with the medical portal."
                )
                return
            
            if not messages:
                await update.message.reply_text("📭 No messages found in your inbox.")
                return
            
            # Store messages in context for back button functionality
            context.user_data['messages_list'] = messages
            
            # Create message list display using unified method
            message_text, reply_markup = self._create_message_list_display(messages)
            
            await update.message.reply_text(
                message_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            
        except Exception as e:
            logger.error(f"Error fetching messages: {e}", exc_info=True)
            await update.message.reply_text(
                "❌ Error fetching messages. Please try again or use /auth to re-authenticate."
            )
    
    async def ask_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /ask command - submit question to doctor."""
        if not self._is_authorized_chat(update):
            return
        
        if not context.args:
            await update.message.reply_text(
                "❌ Please provide a question. Usage: /ask <your question>"
            )
            return
        
        question = ' '.join(context.args)
        if len(question) > 600:
            await update.message.reply_text(
                "❌ Question is too long. Maximum 600 characters allowed."
            )
            return
        
        await update.message.reply_text("📤 Submitting your question...")
        
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                self.executor, self._ask_question_sync, question
            )
            
            if success is None:
                # Authentication required but not available
                await update.message.reply_text(
                    "🔐 Authentication required. Please use /auth to authenticate with the medical portal."
                )
            elif success:
                await update.message.reply_text(
                    "✅ Your question has been submitted successfully!"
                )
            else:
                await update.message.reply_text(
                    "❌ Failed to submit question. Please try again or use /auth to re-authenticate."
                )
                
        except Exception as e:
            logger.error(f"Error submitting question: {e}", exc_info=True)
            await update.message.reply_text(
                "❌ Error submitting question. Please try again."
            )
    
    async def start_auth(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Start authentication process."""
        if not self._is_authorized_chat(update):
            return ConversationHandler.END
        
        await update.message.reply_text("🔐 Starting authentication...")
        
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                self.executor, self._begin_authentication_sync
            )
            
            if result is True:
                await update.message.reply_text("✅ Authentication successful!")
                return ConversationHandler.END
            elif isinstance(result, TwoFactorAuthData):
                # Store 2FA data in context for later use
                context.user_data['twofa_data'] = result
                await update.message.reply_text(
                    "📱 2FA required. Please enter the SMS code you received:"
                )
                return WAITING_FOR_SMS_CODE
            else:
                await update.message.reply_text("❌ Authentication failed. Please try again.")
                return ConversationHandler.END
                
        except Exception as e:
            logger.error(f"Error during authentication: {e}", exc_info=True)
            await update.message.reply_text("❌ Authentication failed. Please try again.")
            return ConversationHandler.END
    
    async def handle_sms_code(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Handle SMS code input for 2FA."""
        if not self._is_authorized_chat(update):
            return ConversationHandler.END
        
        sms_code = update.message.text.strip()
        logger.info(f"Received SMS code for 2FA from user {update.effective_user.id}")
        
        # Get stored 2FA data
        twofa_data = context.user_data.get('twofa_data')
        if not twofa_data:
            logger.warning("No 2FA session found in user data")
            await update.message.reply_text("❌ No 2FA session found. Please start authentication again.")
            return ConversationHandler.END
        
        try:
            loop = asyncio.get_event_loop()
            success = await loop.run_in_executor(
                self.executor, self._complete_two_factor_auth_sync, twofa_data, sms_code
            )
            
            if success:
                # Clear stored 2FA data
                context.user_data.pop('twofa_data', None)
                logger.info("2FA authentication successful")
                await update.message.reply_text("✅ 2FA successful! Authentication completed.")
                return ConversationHandler.END
            else:
                logger.warning("Invalid SMS code provided")
                await update.message.reply_text(
                    "❌ Invalid SMS code. Please try again or use /cancel to abort."
                )
                return WAITING_FOR_SMS_CODE
                
        except Exception as e:
            logger.error(f"Error during 2FA: {e}", exc_info=True)
            await update.message.reply_text("❌ 2FA failed. Please try again.")
            return WAITING_FOR_SMS_CODE
    
    async def cancel_auth(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """Cancel authentication process."""
        if not self._is_authorized_chat(update):
            return ConversationHandler.END
        
        await update.message.reply_text("❌ Authentication cancelled.")
        return ConversationHandler.END
    
    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle callback queries from inline buttons."""
        if not self._is_authorized_chat(update):
            return
        
        query = update.callback_query
        await query.answer()
        
        if query.data == "back_to_messages":
            # Handle back button - show message list again
            messages = context.user_data.get('messages_list', [])
            
            if not messages:
                await query.edit_message_text(
                    "❌ No message list found. Please use /messages to refresh.",
                    parse_mode='Markdown'
                )
                return
            
            # Create message list display using unified method
            message_text, reply_markup = self._create_message_list_display(messages)
            
            await query.edit_message_text(
                message_text,
                parse_mode='Markdown',
                reply_markup=reply_markup
            )
            
        elif query.data.startswith("msg_"):
            message_id = query.data[4:]  # Remove "msg_" prefix
            
            try:
                # Get message details
                loop = asyncio.get_event_loop()
                message_details = await loop.run_in_executor(
                    self.executor, self._get_message_details_sync, message_id
                )
                
                if message_details:
                    # Format message details
                    details_text = f"📄 *Message Details*\n\n"
                    if message_details.get('subject'):
                        details_text += f"*Subject:* {message_details['subject']}\n"
                    if message_details.get('date'):
                        details_text += f"*Date:* {message_details['date']}\n"
                    if message_details.get('sender'):
                        details_text += f"*From:* {message_details['sender']}\n\n"
                    if message_details.get('content'):
                        details_text += f"*Content:*\n{message_details['content']}\n"
                    
                    if message_details.get('attachments'):
                        details_text += f"\n*Attachments:*\n"
                        for att in message_details['attachments']:
                            details_text += f"• {att['name']}\n"
                    
                    # Add back button
                    back_button = InlineKeyboardButton("⬅️ Back to Messages", callback_data="back_to_messages")
                    keyboard = InlineKeyboardMarkup([[back_button]])
                    
                    await query.edit_message_text(
                        details_text,
                        parse_mode='Markdown',
                        reply_markup=keyboard
                    )
                else:
                    await query.edit_message_text("❌ Could not retrieve message details.")
                    
            except Exception as e:
                logger.error(f"Error getting message details: {e}", exc_info=True)
                await query.edit_message_text("❌ Error retrieving message details.")
    
    def _create_message_list_display(self, messages: List[Dict[str, Any]]) -> tuple[str, InlineKeyboardMarkup]:
        """Create message list display text and keyboard."""
        # Create inline keyboard with message buttons
        keyboard = []
        for i, msg in enumerate(messages[:10]):  # Limit to 10 messages
            button_text = f"📄 {msg.get('subject', 'E-consult')[:30]}..."
            if msg.get('date'):
                button_text += f" ({msg['date']})"
            
            keyboard.append([InlineKeyboardButton(
                button_text,
                callback_data=f"msg_{msg.get('id', i)}"
            )])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = f"📋 *Your Messages* ({len(messages)} total)\n\n"
        message_text += "Click on a message to view details:"
        
        return message_text, reply_markup

    async def error_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors."""
        logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    
    # Synchronous wrapper methods for medical portal operations
    def _get_messages_sync(self) -> Optional[List[Dict[str, Any]]]:
        """Synchronous wrapper for getting messages."""
        if not self._ensure_authenticated():
            return None  # Authentication required but not available
        return self.medical_client.list_messages('inbox')
    
    def _get_message_details_sync(self, message_id: str) -> Optional[Dict[str, Any]]:
        """Synchronous wrapper for getting message details."""
        if not self._ensure_authenticated():
            return None
        
        # Find message by ID in the messages list
        messages = self.medical_client.list_messages('inbox')
        for msg in messages:
            if msg.get('id') == message_id:
                if msg.get('url'):
                    return self.medical_client.get_message_details(msg['url'])
        return None
    
    def _ask_question_sync(self, question: str) -> Optional[bool]:
        """Synchronous wrapper for asking questions."""
        if not self._ensure_authenticated():
            return None  # Authentication required but not available
        return self.medical_client.ask_question(question)
    
    def _ensure_authenticated(self) -> bool:
        """Ensure we have a valid authenticated session without triggering SMS."""
        # First check if we already have a valid session
        if self.medical_client.is_authenticated and self.medical_client._is_session_valid():
            return True
        
        # If no valid session, we need to authenticate
        # This will trigger SMS if 2FA is enabled
        logger.warning("No valid session found, authentication required")
        return False
    
    def _begin_authentication_sync(self):
        """Synchronous wrapper for beginning authentication."""
        return self.medical_client.begin_authentication(
            self.medical_email, 
            self.medical_password
        )
    
    def _complete_two_factor_auth_sync(self, twofa_data: TwoFactorAuthData, sms_code: str) -> bool:
        """Synchronous wrapper for completing 2FA authentication."""
        return self.medical_client.complete_two_factor_auth(twofa_data, sms_code)
    
    async def check_for_new_messages(self) -> None:
        """Check for new messages and send notifications."""
        if not self.message_checker_running:
            return
        
        try:
            logger.info("Checking for new messages...")
            
            # Get current messages
            loop = asyncio.get_event_loop()
            messages = await loop.run_in_executor(
                self.executor, self._get_messages_sync
            )
            
            if messages is None:
                # Authentication required but not available - skip this check
                logger.warning("Authentication required for message checking, skipping...")
                return
            
            if not messages:
                return
            
            # Find new messages
            new_messages = []

            new_last_message_id = 0
            for msg in messages:
                if not msg.get('answered'):
                    logger.info(f"Message {msg.get('id')} is not answered, skipping")
                    continue
                msg_id = int(msg.get('id'))
                new_last_message_id = max(new_last_message_id, msg_id)
                if self.last_message_id and msg_id > self.last_message_id:
                    new_messages.append(msg)
            
            # Send notifications for new messages
            for msg in reversed(new_messages):  # Send oldest first
                await self._send_message_notification(msg)

            self.last_message_id = new_last_message_id
            self._save_last_message_state(new_last_message_id)
            
        except Exception as e:
            logger.error(f"Error checking for new messages: {e}", exc_info=True)
    
    async def _send_message_notification(self, message: Dict[str, Any]) -> None:
        """Send notification about a new message."""
        try:
            # Get full message details
            loop = asyncio.get_event_loop()
            details = await loop.run_in_executor(
                self.executor, self._get_message_details_sync, message.get('id', '')
            )
            
            if details:
                notification_text = f"🔔 *New Message Received*\n\n"
                if details.get('subject'):
                    notification_text += f"*Subject:* {details['subject']}\n"
                if details.get('date'):
                    notification_text += f"*Date:* {details['date']}\n"
                if details.get('sender'):
                    notification_text += f"*From:* {details['sender']}\n\n"
                if details.get('content'):
                    # Truncate content if too long
                    content = details['content']
                    if len(content) > 1000:
                        content = content[:1000] + "..."
                    notification_text += f"*Content:*\n{content}\n"
                
                await self.application.bot.send_message(
                    chat_id=self.chat_id,
                    text=notification_text,
                    parse_mode='Markdown'
                )
            else:
                # Fallback notification
                notification_text = f"🔔 *New Message Received*\n\n"
                if message.get('subject'):
                    notification_text += f"*Subject:* {message['subject']}\n"
                if message.get('date'):
                    notification_text += f"*Date:* {message['date']}\n"
                
                await self.application.bot.send_message(
                    chat_id=self.chat_id,
                    text=notification_text,
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Error sending message notification: {e}", exc_info=True)
    
    async def message_checker_worker(self) -> None:
        """Background worker that checks for new messages periodically."""
        self.message_checker_running = True
        logger.info("Message checker worker started")
        
        while self.message_checker_running:
            try:
                await self.check_for_new_messages()
            except Exception as e:
                logger.error(f"Error in message checker worker: {e}", exc_info=True)
                # Add a small delay on error to prevent rapid retries
                await asyncio.sleep(30)
            
            # Wait for the specified interval
            try:
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                logger.info("Message checker worker cancelled")
                break
        
        logger.info("Message checker worker stopped")
    
    async def start_bot(self) -> None:
        """Start the bot and all workers."""
        logger.info("Starting Telegram Medical Portal Bot...")
        
        # Start the bot first
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        
        logger.info("Bot started successfully!")
        
        # Start the message checker worker as a background task
        message_checker_task = asyncio.create_task(self.message_checker_worker())
        
        # Keep running with proper event loop management
        try:
            # Use asyncio.gather to run both the bot and message checker concurrently
            await asyncio.gather(
                message_checker_task,
                return_exceptions=True
            )
        except KeyboardInterrupt:
            logger.info("Shutting down bot...")
            message_checker_task.cancel()
            await self.shutdown()
    
    async def shutdown(self) -> None:
        """Shutdown the bot gracefully."""
        self.message_checker_running = False
        await self.application.updater.stop()
        await self.application.stop()
        await self.application.shutdown()
        logger.info("Bot shutdown complete")


async def main():
    """Main function to run the bot."""
    try:
        bot = TelegramMedicalBot()
        await bot.start_bot()
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    asyncio.run(main())
