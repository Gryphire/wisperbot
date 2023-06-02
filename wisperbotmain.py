#!/usr/bin/python
## LIBRARIES
from datetime import datetime
import glob
import logging
import os
import time
import subprocess
import sys
import re
import openai
from operator import itemgetter
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove     
from telegram.ext import (filters, MessageHandler, ApplicationBuilder, CommandHandler, ContextTypes, CallbackContext)

## TOKEN SETUP
TOKEN = 'insert your bot token here'
openai.api_key = 'insert your openAI api key here

## LOGGER FILE SETUP
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
markup = ReplyKeyboardMarkup(prompt_reply_keyboard, resize_keyboard=True, one_time_keyboard=True)

## COMMANDS
# BOT'S RESPONSE TO /START
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Set up logging for each conversation
    chat_id=update.effective_chat.id
    file_handler = logging.FileHandler(f'chat_{chat_id}.log')
    logger.addHandler(file_handler)
    # When the user presses 'start' to start a conversation with the bot, then...
    # the bot will reply with the following reply text
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Hi {update.message.from_user.first_name}! Welcome to WisperBot.\n\nIf you want to start a new Wisper journey, please use the /prompt command to receive a new prompt and start sending each other voice notes around the prompt!\n\nIf you don't remember where you left off in the conversation, use the /latest command to refresh your memory and send a voicenote of your own.\n\nFor more information about the aim of WisperBot, please use the /help command."
    )
    logger.info(f"Sent Start instructions to {update.message.from_user.first_name}")

# BOT'S RESPONSE TO /HELP
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # When the user presses 'start' to start a conversation with the bot, then...
    # the bot will reply with the following reply text
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"I'm happy to tell you some more about WisperBot!\n\nWisperBot is a product of the Games for Emotional and Mental Health Lab, and has been created to facilitate asynchronous audio conversations between you and others around topics that matter to you.\n\nThrough the prompts that WisperBot provides, the bot's purpose is to help you connect with others in a meaningful way.\n\nNot sure how to get started? Use the /start command to review the instructions."
    )
    logger.info(f"Sent help info to {update.message.from_user.first_name}")


# BOT's RESPONSE TO /PROMPT
async def prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # When the user uses the /prompt commant, then...
    # the bot will reply with the following reply text
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Yay! Happy to hear you'd like to receive a prompt to get going, {update.message.from_user.first_name}!\n\nWhat sort of prompt would you like to receive? Something about...",
        reply_markup = markup
    )
    logger.info(f"Sent new prompt to {update.message.from_user.first_name}")


## MESSAGES
# COMPILING RESPONSE TO USER MESSAGE
## Note: this function only will determine WHAT to send back, if will not send anything back YET
def create_response(usertext: str) -> str:
    # Python is difficult about case, so we want to make sure it's all equalized to lowercase 
    # (which is what we're defining it to look out for) before it hits processing
    processed_usertext: str = usertext.lower()

    # Check if user is greeting the bot, if so respond with a greeting too
    if 'hello' in processed_usertext or 'hi' in processed_usertext or 'hey' in processed_usertext:
        return f"Hi there! Welcome to WisperBot. Please use the /start command to get instructions on how to get started!"

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
    # An integration with OpenAI's DaVinci LLM! Yay! That way the interaction is smoother and the user doesn't keep running into
    # a wall whenever they say something that our pre-set message detection doesn't recognize.
    else: 
        response = openai.Completion.create(
            model="text-davinci-003",
            prompt=processed_usertext,
            temperature = 0.5,
            max_tokens=1024,
            top_p = 1,
            frequency_penalty=0.0
        )
        return response['choices'][0]["text"]

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
            logger.info(f"Sent response to {update.message.from_user.first_name}")
        else:
            return
    else:
        # Respond as usual without checking if WisperBot's name is called
        await context.bot.send_message(
                chat_id = update.effective_chat.id,
                text = create_response(usertext),
            )
        logger.info(f"Sent response to {update.message.from_user.first_name}")

async def transcribe(filename):
    txt = f"{filename}.txt"
    #webm = f"{filename}.webm"
    webm = 'temp.webm'
    # Telegram defaults to opus in ogg, but OpenAI requires opus in webm.
    # We can seemingly double the speech rate (halving the price) without affecting transcription quality.
    subprocess.run([
        'ffmpeg','-i',f'file:{filename}','-c:a','libopus','-b:a','64k','-af','atempo=2','-y',webm,
        ], check=True)
    audio_file = open(webm,'rb')
    transcript = openai.Audio.transcribe('whisper-1',audio_file).pop('text')
    with open(txt,"w",encoding="utf-8") as f:
        f.write(transcript)
    logger.info(f"Transcribed {filename} to {filename}.txt")
    os.remove('temp.webm')
    return transcript

async def get_voice(update: Update, context: CallbackContext) -> None:
    '''Save any voicenotes sent to the bot, and send back the last 5'''
    
    # Save the filename of the last voicenote before saving the new one
    # Using Try/Except because otherwise it throws an error and breaks when there are no voice notes yet
    #try:
    #    vn = get_last_vn() 
    #except: IndexError
    
    # get basic info about the voice note file and prepare it for downloading
    new_file = await context.bot.get_file(update.message.voice.file_id)
    # download the voice note as a file
    ts = datetime.now().strftime("%Y%m%d-%H:%M")
    filename = f"{ts}-{update.message.from_user.first_name}-{new_file.file_unique_id}.ogg"
    await new_file.download_to_drive(filename)
    logger.info(f"Downloaded voicenote as {filename}")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    time.sleep(2) # stupid but otherwise it looks like it responds before it gets your message
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Thanks for recording, {update.message.from_user.first_name}!"
    )
    transcript = await transcribe(filename)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Transcription: {transcript}"
    )
    #await context.bot.send_message(
    #    chat_id=update.effective_chat.id,
    #    text=f"Here are the five most recent voicenotes preceding the one you just submitted:"
    #)
    #await context.bot.send_voice(
    #    chat_id=update.effective_chat.id,
    #    voice=vn
    #)
    #concat_voicenotes()

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

    string_elements =  vn.split('-')
    name = string_elements[2]
    date = datetime.strptime(string_elements[0], '%Y%m%d').strftime('%d/%m/%Y')

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"Hi {update.message.from_user.first_name}! Sure, I'm happy to refresh your memory!\n\n\
Please listen to this latest voicenote, which was sent by {name} on {date}, and respond with your own voicenote."
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
    # /Help command
    application.add_handler(CommandHandler('help', help_command))
    # /Prompt command
    application.add_handler(CommandHandler('prompt', prompt_command))

    # Run application and wait for messages to come in
    application.run_polling()
