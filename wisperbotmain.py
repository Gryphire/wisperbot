
### SETTING WISPERBOT'S BEHAVIOURS

## LIBRARIES
from typing import Final        #Enables us to indicate that a variable should NOT be reassigned or overridden
from telegram import Update     #Enables us to push an update to the Telegram API, e.g., sending a message, etc.
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

## SETUP VARS
TOKEN: Final ='6229904915:AAFD6r03dFf61YkCDVRJxnt7sSKSqKWSABA'
BOT_USERNAME: Final = '@wisper_social_bot'

## COMMANDS
# BOT's START-BEHAVIOUR
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # When the user presses 'start' to start a conversation with the bot, then...
    # the bot will reply with the following reply text
    await update.message.reply_text("Welcome! I'm looking forward to helping you connect and reflect with others!")

# BOT's HELP-BEHAVIOUR
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # When the user types /help in the conversation with the bot, then...
    # the bot will reply with the following reply text
    await update.message.reply_text("I'm happy to help! Just record a voice message resonding to the prompt and hit enter to send it to your chat partners!")

# BOT's CUSTOM COMMAND BEHAVIOUR
#async def custom_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
#    # When the user types /custom in the conversation with the bot, then...
#    # the bot will reply with the following reply text
#    await update.message.reply_text("Ohh, you're trying a custom command! Exciting!")

## CREATE RESPONSE TO USER MESSAGE
## Note: this function only will determine WHAT to send back, if will not send anything back YET
def create_response(usertext: str) -> str:
    # Python is difficult about case, so we want to make sure it's all equalized to lowercase 
    # (which is what we're defining it to look out for) before it hits processing
    processed_usertext: str = usertext.lower()

    # Check if user is greeting the bot, if so respond with a greeting too
    if 'hello' in processed_usertext or 'hi' in processed_usertext:
        return: "Hi there!"

    # Check if user is asking how the bot is doing
    if 'how are you?' in processed_usertext:
        return: "I'm doing good, to the extent that that's possible for a bot! Hope you're having a lovely day too!"

    # If none of these are detected (i.e., the user is saying something else), respond with...
    return: "Sadly I'm unable to understand what you're telling me. Maybe in a next upgrade!"

## SENDING BOT'S RESPONSE THROUGH MESSAGE
async def handle_message(update: Update, context: ContextType.DEFAULT_TYPE):
    # First we need to determine the chat type: solo with bot, or group chat with bot?
    # This is important because in a group chat, people may not be talking to the bot, and we want to 
    # ensure that the bot only responds when spoken to.
    chat_type: str = update.message.chat.type
    usertext: str = update.message.text

    # Add some debugging log to console so we can see what's happening
    print(f'User ({update.message.chat.id}) in {chat_type}: "{usertext}"')

    # Response handling in case it's a group chat
    if chat_type = 'group':
        # Check if the bot is being addressed by the user
        if BOT_USERNAME in usertext:
            # Filter out the bot's user name out of the user's message and strip from leading or trailing white spaces,
            # so we can process the user's message correctly
            clean_usertext: str = usertext.replace(BOT_USERNAME, '').strip
            response: str = create_response(clean_usertext)
        else: 
            # If bot's name is not mentioned in user's message, do nothing
            return
    # Response handling in case it's a solo chat
    else:
        # No cleaning/filtering necessary, we can just respond to the user's message as it came in
        response: str = create_response(usertext)

    print('Bot: ', response)

    # ACTUALLY send the response to the chat
    await update.message.reply_text(response)

## ERROR HANDLING
async def error(update: Update, context: ContextType.DEFAULT_TYPE):
    print(f'The update {update} caused the following error: {context.error}')

## PUTTING ALL FUNCTIONS TOGETHER IN AN APP
if __name__ == '__wisperbotmain__':
    # Get the app up and running
    print('Starting WisperBot...')
    app = Application.builder().token(TOKEN).build()
    
    # Connect our commands to the app
    app.add_handler(CommandHandler('start', start_command))
    app.add_handler(CommandHandler('help', help_command))
    #app.add_handler(CommandHandler('custom', custom_command))

    # Connect our message-handling to the app
    app.add_handler(MessageHandler(filters.TEXT, handle_message))

    # Connect our error-handling to the app
    app.add_error_handler(error)

    # IMPORTANT: Check for new updates coming from the chat
    # We set it to check for new messages etc. from the chat every 3 seconds
    print('Waiting for updates...')
    app.run_polling(poll_interval=3)
