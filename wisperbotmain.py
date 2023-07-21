#!/usr/bin/python
## LIBRARIES
from datetime import datetime
import glob
import logging
import os
import time
import subprocess
import random
#import urllib.request
from operator import itemgetter
import dotenv
import openai
import pandas as pd
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (filters, MessageHandler, ApplicationBuilder,
                          CommandHandler, ContextTypes, CallbackContext)

## TOKEN SETUP
dotenv.load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN")
openai.api_key = os.environ.get("OPENAI_API_KEY")

## PROMPT FILE SETUP
# Import prompts file
fullpath = os.getcwd() + "/prompts.csv"
promptdf = pd.read_csv(fullpath)

## LOGGER FILE SETUP
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO)

class HTTPXFilter(logging.Filter):
    '''Filter out lines starting with HTTP'''
    def filter(self, record):
        return not record.msg.startswith("HTTP")

top_level_logger = logging.getLogger("top_level")
top_level_handler = logging.FileHandler("central_log.log")
top_level_formatting = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
top_level_handler.setFormatter(top_level_formatting)
top_level_logger.setLevel(logging.INFO)
top_level_logger.addHandler(top_level_handler)
top_level_logger.addFilter(HTTPXFilter())
top_level_logger.propagate = False
# Save logger to object that we can call in future to save events to
logging.getLogger("httpx").addFilter(HTTPXFilter())
# Keep a dictionary of loggers:
chat_handlers = {}

class ChatHandler:
    def __init__(self, chat_id, update=None, context=None):
        self.chat_id = chat_id
        self.chat_type = update.message.chat.type
        self.context = context
        self.prompt = None
        if self.chat_type == 'private':
            self.name = update.message.from_user.full_name
        elif 'group' in self. chat_type: # To inlude both group and supergroup
            self.name = update.message.chat.title
        self.logger = self.get_logger()

    @property
    def directory(self):
        dest_dir = f'files/{self.chat_type}/{self.name}-{self.chat_id}'
        try:
            os.makedirs(f'{dest_dir}')
        except FileExistsError:
            pass
        return dest_dir

    def get_logger(self):
        log_file = f'{self.directory}/chat_{self.chat_id}'
        formatting = formatting = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler = logging.FileHandler(log_file)
        handler.setFormatter(formatting)
        logger = logging.getLogger(f"chat-{self.chat_id}")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        logger.info(f'Now logging to {log_file}')
        return logger

    def log(self, msg):
        '''Log a message to the correct log file'''
        self.logger.info(msg)
        top_level_logger.info(f"chat-{self.chat_id} {self.name}: {msg}")

    async def send_msg(self, text, reply_markup = None):
        '''Send a message in this chat'''
        await self.context.bot.send_message(self.chat_id, text, reply_markup = reply_markup)

    async def send_img(self, photo):
        await self.context.bot.send_photo(self.chat_id,photo)
    
# BOT'S PROMPT CHOICE SYSTEM
# Determine what question is asked
prompt_reply_keyboard = [
    ["A Value Tension ⚖️, Wisper", "Authentic Relating 🤝, Wisper!"],
]
# Format the 'keyboard' (which is actually the multiple choice field)
markup = ReplyKeyboardMarkup(prompt_reply_keyboard, resize_keyboard=True, one_time_keyboard=True)

## COMMANDS
# BOT'S RESPONSE TO /START
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Start a session when the bot receives /start'''
    # Activate
    chat = await initialize_chat_handler(update,context)
    # When the user presses 'start' to start a conversation with the bot, then...
    # the bot will reply with the following reply text
    await chat.send_msg(text=f"Hi {update.message.from_user.first_name}! Welcome to WisperBot.\n\nWisperBot is created to help you connect with others through asynchronous audio-conversations! Wisper is all about sending each other voice messages, listening, and responding. In particular, we want to encourage you to really try to hear what someone is saying: in your conversation, try to reflect back on what the other person said, be respectful in your response, and ask clarifying questions to get to know the other person better!\n\nIf you want to start a new Wisper journey, please use the /prompt command to receive a new prompt and start sending each other voice notes around the prompt!\n\nIf you don't remember where you left off in the conversation, use the /latestprompt command to refresh your memory of which prompt you are using. You can also use the /latestaudio command to be able to listen to the last Wisper that was sent to you, so that you can get back into the conversation!\n\nAre you using WisperBot in a group chat? Make sure that you include the bot's name (e.g., Wisper or WisperBot) in your message so the bot knows that you're talking to it and not one of your fellow group members. (:\n\nFor more information about the aim of WisperBot, please use the /help command. Happy Wispering!"
    )
    chat.log(f"Sent Start instructions to {chat.name}")

# BOT'S RESPONSE TO /HELP
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # When the user presses 'start' to start a conversation with the bot, then...
    # the bot will reply with the following reply text
    chat = await initialize_chat_handler(update,context)
    await chat.send_msg(text=f"I'm happy to tell you some more about WisperBot!\n\nWisperBot is a product of the Games for Emotional and Mental Health Lab, and has been created to facilitate asynchronous audio conversations between you and others around topics that matter to you.\n\nThrough the prompts that WisperBot provides, the bot's purpose is to help you connect with others in a meaningful way.\n\nNot sure how to get started? Use the /start command to review the instructions.")
    chat.log(f"Sent help info to {update.message.from_user.first_name}")

# BOT's RESPONSE TO /PROMPT
async def prompt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # When the user uses the /prompt commant, then...
    # the bot will reply with the following reply text
    chat = await initialize_chat_handler(update,context)
    await chat.send_msg(text=f"Yay! Happy to hear you'd like to receive a prompt to get going, {update.message.from_user.first_name}!\n\nWhat sort of prompt would you like to receive? Something about...",
        reply_markup = markup
    )
    chat.log(f"Sent new prompt to {update.message.from_user.first_name}")

## MESSAGES
# COMPILING RESPONSE TO USER MESSAGE
## Note: this function only will determine WHAT to send back, if will not send anything back YET
## It will also store the latest prompt if it detects one as an attribute to the chat object
## Might be a better way to separate out the handling of which prompt is chosen into another function?
def create_response(chat, usertext: str) -> str:
    # Python is difficult about case, so we want to make sure it's all equalized to lowercase 
    # (which is what we're defining it to look out for) before it hits processing
    processed_usertext: str = usertext.lower()

    # Check if user is greeting the bot, if so respond with a greeting too
    if 'hello' in processed_usertext or 'hi' in processed_usertext or 'hey' in processed_usertext:
        response = f"Hi there! Welcome to WisperBot. Please use the /start command to get instructions on how to get started!"

    # Check if user is asking how the bot is doing
    elif 'how are you' in processed_usertext:
        response = f"I'm doing good, to the extent that that's possible for a bot! Hope you're having a lovely day too!"

    # Check if user is thanking WisperBot
    elif 'thanks' in processed_usertext or 'thank you' in processed_usertext:
        response = f"You're very welcome! Remember, if you get stuck, you can always get back on track with /start or /help."

    # PROMPT RESPONSES
    # Value tensions
    elif 'value tension ⚖️' in processed_usertext:
        # Randomly select a prompt from the Value Tension category from our prompt dataframe
        randomVTprompt = random.choice(promptdf.prompt[promptdf.category.eq('value tension')].tolist())
        # Include random prompt in response
        response =  f"Amazing. Here is my prompt for you around a value tension:\n\n\U0001F4AD {randomVTprompt}\n\nHave fun chatting!"
        # Save chosen prompt
        chat.prompt = randomVTprompt
        ReplyKeyboardRemove()

    # Authentic relating
    elif 'authentic relating 🤝' in processed_usertext:
        response = f"Cool. Let's see.. Here is my prompt around authentic relating:\n\n\U0001F4AD Try to be authentic!\n\nHave fun chatting!"
        chat.prompt = "Authentic Relating"
    
    # INSTEAD WHEN THE BOT ENCOUNTERS UNSCRIPTED INPUT FROM USER, DO THIS...
    else:
        response = f"Sorry, I'm not sure I understand. Remember, I'm only here to facilitate your audio message conversations by providing a prompt whenever you need me to. Unsure how to get started? Use the /start command to get instructions. ^^"

    """ CURRENTLY COMMENTING OUT THE OPENAI RESPONSES DUE TO IT DISTRACTING FROM AUDIO MESSAGING
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
        response = response['choices'][0]["text"] """

    if chat.prompt:
        chat.log(f"{chat.name} chose prompt {chat.prompt}")

    return response

# HANDLING MESSAGE RESPONSE
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Given a message, echo it back with their name'''
    # First we need to determine the chat type: solo with bot, or group chat with bot?
    # This is important because in a group chat, people may not be talking to the bot, and we want to 
    # ensure that the bot only responds when spoken to.
    chat = await initialize_chat_handler(update,context)
    usertext: str = update.message.text
    processed_usertext: str = usertext.lower()

    if chat.chat_type == 'group':
        # Make sure to only respond when reference is made to WisperBot
        if 'wisper' in processed_usertext or 'wisperbot' in processed_usertext:
            # Only respond if Wisperbot's name is called in user's message
            await chat.send_msg(text=create_response(chat,usertext))
            chat.log(f"Sent response to {update.message.from_user.first_name}")
        else:
            return
    else:
        # Respond as usual without checking if WisperBot's name is called
        await chat.send_msg(text=create_response(chat,usertext))
        chat.log(f"Sent response to {update.message.from_user.first_name}")

async def transcribe(update: Update,filename):
    chat = await initialize_chat_handler(update)
    txt = f"{filename}.txt"
    #webm = f"{filename}.webm"
    webm = 'temp.webm'
    # Telegram defaults to opus in ogg, but OpenAI requires opus in webm.
    # We can seemingly double the speech rate (halving the price) without affecting transcription quality.
    cmd = f'ffmpeg -i file:"{filename}" -c:a libopus -b:a 64k -af atempo=2 -y {webm}'
    subprocess.check_output(cmd, shell=True)
    audio_file = open(webm,'rb')
    transcript = openai.Audio.transcribe('whisper-1',audio_file).pop('text')
    with open(txt,"w",encoding="utf-8") as f:
        f.write(transcript)
    chat.log(f"Transcribed {filename} to {filename}.txt")
    os.remove('temp.webm')
    return transcript

async def get_voice(update: Update, context: CallbackContext) -> None:
    '''Save any voicenotes sent to the bot, and send back the last 5'''
    
    chat = await initialize_chat_handler(update,context)
    # Save the filename of the last voicenote before saving the new one
    # Using Try/Except because otherwise it throws an error and breaks when there are no voice notes yet
    #try:
    #    vn = get_last_vn() 
    #except: IndexError
    # get basic info about the voice note file and prepare it for downloading
    new_file = await context.bot.get_file(update.message.voice.file_id)
    # download the voice note as a file
    ts = datetime.now().strftime("%Y%m%d-%H:%M")
    dest_dir = chat.directory
    filename = f"{dest_dir}/{ts}-{update.message.from_user.first_name}-{new_file.file_unique_id}.ogg"
    await new_file.download_to_drive(filename)
    chat.log(f"Downloaded voicenote as {filename}")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    time.sleep(2) # stupid but otherwise it looks like it responds before it gets your message
    #await chat.send_msg(text=f"Thanks for recording, {update.message.from_user.first_name}!")
    transcript = await transcribe(update,filename)
    # Have commented this out because I don't want people to he able to read the transcript
    #await chat.send_msg(text=f"Transcription: {transcript}")
    
    #await context.bot.send_message(
    #    chat_id=update.effective_chat.id,
    #    text=f"Here are the five most recent voicenotes preceding the one you just submitted:"
    #)
    #await context.bot.send_voice(
    #    chat_id=update.effective_chat.id,
    #    voice=vn
    #)
    #concat_voicenotes()

"""  ## IMAGE GENERATING SECTION OF THE GET-VOICE FUNCTION
    ## Return keywords of transcription
    keywords = openai.Completion.create(
        model="text-davinci-003",
        prompt="Extract keywords from this text:" + transcript,
        temperature=0.5,
        max_tokens=60,
        top_p=1.0,
        frequency_penalty=0.8,
        presence_penalty=0.0
    )
    keywordslist = keywords['choices'][0]["text"]
    await chat.send_msg(f"These keywords have been extracted from your voice note: " + keywordslist)
    chat.log(f"Extracted keywords {keywordslist} from voicenote {filename}")

    ## Generate a DALL-E prompt based on these keywords
    imgprompt = openai.Completion.create(
        model="text-davinci-003",
        prompt="Generate a prompt for a beautiful AI image using these keywords:" + keywordslist,
        temperature=0.5,
        max_tokens=60,
        top_p=1.0,
        frequency_penalty=0.8,
        presence_penalty=0.0
    )
    imgpromptresult = imgprompt['choices'][0]["text"]
    chat.log(f"Generated DALL-E image prompt from keywords of {filename}")

    ## Generate actual image using this prompt
    AIimg = openai.Image.create(
        prompt = imgpromptresult,
        n = 1,
        size = "1024x1024"
    )
    # Retrieve the URL at which image is stored
    imageURL = AIimg['data'][0]['url']
    # Store online image in local .png file, by first defining the file name
    img_filename = f"{dest_dir}/{ts}-{update.message.from_user.first_name}-{new_file.file_unique_id}.png"
    urllib.request.urlretrieve(imageURL,img_filename)
    # Send image to chat
    await chat.send_img(photo=imageURL)
    chat.log(f"Generated and sent AI image of {filename}") """


async def get_last_vn():
    '''Get the latest ogg file in the current directory'''
    ogg_files = glob.glob("files/*/*/*.ogg")
    file_times = {file:os.path.getmtime(file) for file in ogg_files}
    file_times = sorted(file_times.items(), key=itemgetter(1), reverse=True)

    most_recent_file = file_times[0][0]

    return most_recent_file

""" async def concat_voicenotes():
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
    chat.log(f"Updated latest.ogg") """


async def latestaudio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = await initialize_chat_handler(update,context)
    vn = await get_last_vn()
    string_elements = vn.split('-')
    print(string_elements)
    name = string_elements[3]
    # Commenting this out as the naming convention has changed.
    # We could optionally change it back to include the date or else get the date from the file metadata?
    #date = datetime.strptime(string_elements[0], '%Y%m%d').strftime('%d/%m/%Y')

    await chat.send_msg(text=f"Hi {update.message.from_user.first_name}! Sure, I'm happy to refresh your memory!\n\n\
Please listen to this latest voicenote, which was sent by {name}, and respond with a voicenote.")
    await context.bot.send_voice(
        chat_id=update.effective_chat.id,
        voice=vn
    )
    chat.log(f"Sent {vn} to {update.message.from_user.first_name}")


async def latestprompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = await initialize_chat_handler(update,context)
    prompt = chat.prompt

    if prompt:
        await chat.send_msg(text=f"Hi {update.message.from_user.first_name}! Sure, I'm happy to refresh your memory!\n\nYour latest prompt was: {prompt}")
        chat.log(f"Sent last prompt ({prompt}) to {update.message.from_user.first_name}")
    else:
        await chat.send_msg(text=f"Hi {update.message.from_user.first_name}! You have not yet specified a prompt. Please run /prompt to see the options :)")
        chat.log(f"{update.message.from_user.first_name} asked for the last prompt but had not yet specified one we recognized")

async def initialize_chat_handler(update,context=None):
    chat_id = update.effective_chat.id
    if chat_id not in chat_handlers:
        chat_handlers[chat_id] = ChatHandler(chat_id,update,context)
        chat = chat_handlers[chat_id]
    chat = chat_handlers[chat_id]
    return chat


## COMPILE ALL FUNCTIONS INTO APP
if __name__ == '__main__':
    # Create application
    application = ApplicationBuilder().token(TOKEN).build()

    # Connect our text message and audio handlers to the app
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_msg))
    application.add_handler(MessageHandler(filters.VOICE, get_voice))

    ## Connect our command handlers to the app
    # /Start command
    application.add_handler(CommandHandler('start', start_command))
    # /Latestaudio command
    application.add_handler(CommandHandler('latestaudio', latestaudio))
    # /Latestprompt command
    application.add_handler(CommandHandler('latestprompt', latestprompt))
    # /Help command
    application.add_handler(CommandHandler('help', help_command))
    # /Prompt command
    application.add_handler(CommandHandler('prompt', prompt_command))

    # Run application and wait for messages to come in
    application.run_polling()
