#!/usr/bin/python
## LIBRARIES
import datetime
import glob
import logging
import os
import time
import subprocess
import sys
from operator import itemgetter
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove     
from telegram.ext import (filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes, CallbackContext)

## TOKEN SETUP
TOKEN = '#insert token here'

## LOGGER SETUP
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)
# Save logger to object that we can call in future to save events to
logger = logging.getLogger()

# BOT'S PROMPT CHOICE SYSTEM
# Determine what question is asked
prompt_reply_keyboard = [
    ["Value Tensions", "Authentic Relating"],
]
# Format the 'keyboard' (which is actually the multiple choice field)
markup = ReplyKeyboardMarkup(prompt_reply_keyboard, one_time_keyboard=True)

## COMMANDS
# BOT'S RESPONSE TO /START
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # When the user presses 'start' to start a conversation with the bot, then...
    # the bot will reply with the following reply text
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Hi {update.message.from_user.first_name}! Welcome to WisperBot.\n\nIf you want to start a new Wisper journey, please use the /prompt command to receive a new prompt and start sending each other voice notes around the prompt!\n\nUse the /latest command to receive the latest voicenote, or send me (and anyone else in this group) a voice note of your own.\n\nFor more information about the aim of WisperBot, please use the /help command."
    )
    logger.info(f"Sent instructions to {update.message.from_user.first_name}")

# BOT's RESPONSE TO /PROMPT
async def prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # When the user uses the /prompt commant, then...
    # the bot will reply with the following reply text
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Yay! Happy to hear you'd like to receive a prompt to get going, {update.message.from_user.first_name}!\n\nWhat sort of prompt would you like to receive? Something about...",
        reply_markup = markup
    )
    logger.info(f"Sent prompt to {update.message.from_user.first_name}")


## MESSAGES
# COMPILING RESPONSE TO USER MESSAGE
## Note: this function only will determine WHAT to send back, if will not send anything back YET
def create_response(usertext: str) -> str:
    # Python is difficult about case, so we want to make sure it's all equalized to lowercase 
    # (which is what we're defining it to look out for) before it hits processing
    processed_usertext: str = usertext.lower()

    # Check if user is greeting the bot, if so respond with a greeting too
    if 'hello' in processed_usertext or 'hi' in processed_usertext or 'hey' in processed_usertext:
        return f"Hi there! Welcome to WisperBot. Please use the /start command to get started!"

    # Check if user is asking how the bot is doing
    if 'how are you?' in processed_usertext:
        return f"I'm doing good, to the extent that that's possible for a bot! Hope you're having a lovely day too!"

    # Check if user is thanking WisperBot
    if 'thanks' in processed_usertext or 'thank you' in processed_usertext:
        return f"You're very welcome! Remember, if you get stuck, you can always get back on track with /start or /help."

    # PROMPT RESPONSES
    # Value tensions
    if 'value tensions' in processed_usertext:
        return f"Amazing. Here is my prompt for you around a value tension:\n\n\U0001F4AD How do you balance taking care of others and taking care of yourself?\n\nHave fun chatting!"

    # Authentic relating
    if 'authentic relating' in processed_usertext:
        return f"Cool. Let's see.. Here is my prompt around authentic relating:\n\n\U0001F4AD Try to be authentic!\n\nHave fun chatting!"

    # If none of these are detected (i.e., the user is saying something else), respond with...
    else: 
        return f"Sadly I'm unable to understand what you're telling me. Maybe in a next upgrade!"

# HANDLING MESSAGE RESPONSE
async def msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Given a message, echo it back with their name'''
    # First we need to determine the chat type: solo with bot, or group chat with bot?
    # This is important because in a group chat, people may not be talking to the bot, and we want to 
    # ensure that the bot only responds when spoken to.
    chat_type: str = update.message.chat.type
    usertext: str = update.message.text
    processed_usertext: str = usertext.lower()

    if chat_type == 'group':
        # Make sure to only respond when reference is made to WisperBot
        if 'wisper' in processed_usertext or 'wisperbot' in processed_usertext:
            # Only respond if Wisperbot's name is called in user's message
            await context.bot.send_message(
                chat_id = update.effective_chat.id,
                text = create_response(usertext),
            )
            logger.info(f"Sent instructions to {update.message.from_user.first_name}")
        else:
            return
    else:
        # Respond as usual without checking if WisperBot's name is called
        await context.bot.send_message(
                chat_id = update.effective_chat.id,
                text = create_response(usertext),
            )
        logger.info(f"Sent instructions to {update.message.from_user.first_name}")


async def get_voice(update: Update, context: CallbackContext) -> None:
    '''Save any voicenotes sent to the bot, and send back the last 5'''
    vn = get_last_vn() # Save the filename of the last voicenote before saving the new one
    # get basic info about the voice note file and prepare it for downloading
    new_file = await context.bot.get_file(update.message.voice.file_id)
    # download the voice note as a file
    ts = datetime.datetime.now().strftime("%Y%m%d-%H:%M")
    filename = f"{ts}-{update.message.from_user.first_name}-{new_file.file_unique_id}.ogg"
    await new_file.download_to_drive(filename)
    logger.info(f"Downloaded voicenote as {filename}")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    time.sleep(2) # stupid but otherwise it looks like it responds before it gets your message
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Thanks for recording, {update.message.from_user.first_name}!"
    )
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Here are the voicenotes preceding the one you just submitted:"
    )
    await context.bot.send_voice(
        chat_id=update.effective_chat.id,
        voice=vn
    )
    concat_voicenotes()

def get_last_vn():
    '''Get the latest ogg file in the current directory'''
    ogg_files = glob.glob("*.ogg")
    file_times = {file:os.path.getmtime(file) for file in ogg_files}
    file_times = sorted(file_times.items(), key=itemgetter(1), reverse=True)

    most_recent_file = file_times[0][0]

    return most_recent_file

def concat_voicenotes():
    '''Make the last 5 voicenotes into the latest voicenote'''
    ogg_files = glob.glob("20*.ogg")
    ogg_files.sort(key=os.path.getmtime)
    latest_5 = ogg_files[-5:]

    with open('inputs','w',encoding='utf-8') as f:
        for filename in latest_5:
            f.write(f"file 'file:{filename}'\n")
    subprocess.run([
        'ffmpeg','-f','concat','-safe','0','-i','inputs','-c','copy','-y','latest.ogg',
        ], check=False)
    logger.info(f"Updated latest.ogg")


async def latest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    vn = get_last_vn()
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Hi {update.message.from_user.first_name}! \
Please listen to this voicenote and respond with your own voicenote."
    )
    await context.bot.send_voice(
        chat_id=update.effective_chat.id,
        voice=vn
    )
    logger.info(f"Sent {vn} to {update.message.from_user.first_name}")

## COMPILE ALL FUNCTIONS INTO APP
if __name__ == '__main__':
    # Create application
    application = ApplicationBuilder().token(TOKEN).build()

    # Connect our text message and audio handlers to the app
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), msg))
    application.add_handler(MessageHandler(filters.VOICE , get_voice))

    ## Connect our command handlers to the app
    # /Start command
    application.add_handler(CommandHandler('start', start_command))
    # /Latest command
    application.add_handler(CommandHandler('latest', latest))
    # /Prompt command
    application.add_handler(CommandHandler('prompt', prompt_command))

    # Run application and wait for messages to come in
    application.run_polling()
