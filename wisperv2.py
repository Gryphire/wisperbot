#!/usr/bin/python

## LIBRARIES TO BE IMPORTED
import os
import asyncio
import logging
import random
import sqlite3
import subprocess
from datetime import datetime
import dotenv
import openai
from telegram import Update
from telegram.error import TimedOut
from telegram.ext import (filters, MessageHandler, ApplicationBuilder,
  CommandHandler, ContextTypes, CallbackContext, ConversationHandler)
import re

# MAKE SURE API KEYS ARE USED FROM .ENV FILE
dotenv.load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN")
openai.api_key = os.environ.get("OPENAI_API_KEY")

## Set up for conversation handler
GET_PARTICIPANT_NUMBER = 0
DB = "wisper.db"
#CONFIRM_PARTICIPANT_NUMBER = 1

# SET UP LOGGING
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
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

# save logger to object that we can call in future to save events to
logging.getLogger("httpx").addFilter(HTTPXFilter())

# Keep a dictionary of loggers:
chat_handlers = {}

# CHECK FOR FOLDER TO KEEP TRACK OF WHICH TUTORIAL STORIES HAVE BEEN SENT TO USER ALREADY
os.makedirs('sent', exist_ok=True)

# List of tutorialstories at very start (when none have been sent yet)
# Note that this variable resets everytime the bot restarts!!
unsent_tutorial_files = [f for f in sorted(os.listdir('tutorialstories/')) if f.startswith('tutstory')]

# List of Statuses here https://github.com/Gryphire/wisperbot/issues/23
# Handy if-skeleton for different statuses
""" if self.status == 'start_welcomed':
        ...
    elif self.status == 'tut_started':
        ...
    elif self.status == 'tut_story1received':
        ...
    elif self.status == 'tut_story2received':
        ...
    elif self.status == 'tut_story3received':
        ...
    elif self.status == 'tut_story4received':
        ...
    elif self.status == 'tut_story1responded':
        ...
    elif self.status == 'tut_story2responded':
        ...
    elif self.status == 'tut_story3responded':
        ...
    elif self.status == 'tut_story4responded':
        ...
    elif self.status == 'tut_completed':
        ... """

class ChatHandler:
    def __init__(self, chat_id, update=None, context=None):
        self.chat_id = chat_id
        self.chat_type = update.message.chat.type
        self.context = context
        self.sent = []
        # When chat first starts, the user will be in tutorial mode
        self._status = 'none'
        # Initial voicenotes will be saved under "tutorialresponses"
        self.subdir = 'tutorialresponses'
        if self.chat_type == 'private':
            self.name = update.message.from_user.full_name
            try:
                self.first_name = update.message.from_user.first_name
            except AttributeError:
                self.first_name = self.name
        elif 'group' in self. chat_type: # To inlude both group and supergroup
            self.name = update.message.chat.title
        try:
            with open(f'chat_sessions/chat-{self.chat_id}', 'r', encoding='utf-8') as f:
                self.number = int(f.read())
        except FileNotFoundError:
            self.number = None
        self.logger = self.get_logger()

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        self.log(f'Status changing from "{self.status}" to "{value}"')
        self._status = value

    @property
    def directory(self):
        part = f'{self.chat_type}/{self.name}'
        os.makedirs(f'{part}', exist_ok=True)
        return part

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

    async def choose_random_vn(self):
        exclude = str(self.chat_id)
        ogg_files = [f for f in os.listdir() if f.endswith('.ogg')]
        #Uncomment this so we don't send people's voicenotes back to them:
        ogg_files = [f for f in ogg_files if f not in self.sent and exclude not in f]
        try:
            self.log(f'Sending random personal story to {self.name}')
            random_file = random.choice(ogg_files)
            self.sent.append(random_file)
            self.log(f'Selected random voicenote: {random_file}')
            return random_file
        except IndexError:
            await self.send_msg("""Exciting! You've listened to all the reflections I've got for you so far. Please run /??? to enter the main experience!""")
            self.log(f'No suitable voicenotes to choose from.')
            return None

    async def send_msg(self, text, reply_markup = None):
        '''Send a message in this chat; try infinitely'''
        while True:
            try:
                # Make the Telegram request
                await self.context.bot.send_message(
                    self.chat_id, text, parse_mode='markdown', reply_markup=reply_markup
                )
                # Request succeeded, break the loop
                break
            except TimedOut:
                # Timeout error occured, log a warning
                self.logger.warning("Request timed out, retrying...")

                # Wait a few seconds before retrying
                await asyncio.sleep(5)

    async def send_vn(self,VN):
        '''Send a voicenote file'''
        c.execute("INSERT INTO logs VALUES (?,?,?,?)", 
                  (datetime.now(),
                   self.name, 
                   self.chat_id, 
                   VN))
        conn.commit()
        self.sent.append(VN)
        await self.context.bot.send_voice(chat_id=self.chat_id, voice=VN)
        self.log(f'Sent {VN}')

    async def sqlquery(self,cmd,fetchall=False):
        c.execute(cmd)
        if fetchall:
            result = c.fetchall()
        else:
            result = c.fetchone()
        return result

    # Currently not in use
    #async def send_intro(self):
    #    self.log(f'Chat {self.chat_id} from {self.name}: Sending second instructions')
    #    await self.send_msg("""Entering main sequence...""")

    async def send_endtutorial(self):
        self.log(f'Chat {self.chat_id} from {self.name}: Completed tutorial')
        await self.send_msg("""Amazing, thanks for completing the tutorial!""")

    # Little hacky to have it in a separate function,
    # but I couldn't get it to work within the echo function due to the update and context 
    # (adding self there didn't help)
    async def textresponse_chooser(self):
        # Determine bot's response to user based on their 'location' in the journey
        if self.status == 'start_welcomed':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"It looks like you were about to start the tutorial to practice? If you'd like a little reminder of what this bot is about, use the /start command.\n\nIf, on the other hand, you're ready to start the tutorial, use the /starttutorial command!")
        elif self.status == 'none':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"I see you're new here! Welcome! Please use the /start command to get started.")
        elif self.status == 'tut_started':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"It looks like you have previously received the tutorial instructions, but haven't requested your first tutorial story yet.\n\nIf you'd like a little reminder of the instructions, use the /starttutorial command.\n\nIf you're ready to get your first practice story, use the /gettutorialstory command. (:")
        elif self.status == 'tut_story1received':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"Hmm, looks like you still need to respond to the first tutorial story I sent you earlier.\n\nIf needed, scroll back up to find the audio as well as the instruction that accompany the story. ^^")
        elif self.status == 'tut_story2received':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"Hmm, looks like you still need to respond to the latest tutorial story I sent you earlier.\n\nIf needed, scroll back up to find the audio as well as the instruction that accompany the story. ^^")
        elif self.status == 'tut_story3received':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"Hmm, looks like you still need to respond to the latest tutorial story I sent you earlier.\n\nIf needed, scroll back up to find the audio as well as the instruction that accompany the story. ^^")
        elif self.status == 'tut_story4received':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"Hmm, looks like you still need to respond to the fourth and last tutorial story I sent you earlier.\n\nIf needed, scroll back up to find the audio as well as the instruction that accompany the story. ^^")
        elif self.status == 'tut_story1responded':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"Wow, you're a fast typer!")
        elif self.status == 'tut_story2responded':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"Wow, you're a fast typer!")
        elif self.status == 'tut_story3responded':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"Wow, you're a fast typer!")
        elif self.status == 'tut_story4responded':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"Wow, you're a fast typer!")
        elif self.status == 'tut_completed':
            await self.send_msg(f"""Hey there {self.first_name}! I'm sorry, unfortunately you can't really chat with me, but I'm happy to point you in the right direction if you're unsure what you need to do next in Wisper!""")
            await self.send_msg(f"Awesome job on completing the tutorial! We're currently working on building the rest of the bot, so this is where the experience ends, at the moment.")

    async def send_tutstory(self):
        # Response to user if they try to request a new tutorial story while not having responded to the previous yet
        if self.status == 'tut_story1received' or self.status == 'tut_story2received' or self.status == 'tut_story3received' or self.status == 'tut_story4received':
            await self.send_msg(f"You can't request a new tutorial story yet, because it seems you have not yet sent in your audio response to the last tutorial story.\n\nPlease do that first, and then we will proceed!")
        # Response to user if they try to run /gettutorialstory while not having run /starttutorial yet
        elif self.status == 'start_welcomed' or self.status == 'none': 
            await self.send_msg(f"You can't request a tutorial story yet. Make sure you read the tutorial instructions first.\n\nPlease use the /starttutorial command to proceed. ^^")
        else:
            try:
                # Chooses first 'top' item from unsent list, removes it, assigns to chosenTutStory
                chosenTutStory = unsent_tutorial_files.pop(0)
                # Check if chosenTutStory is already featured in database (if so, it's been sent already)
                presentInDatabase = await self.sqlquery(f'SELECT * FROM logs WHERE filename="{chosenTutStory}" AND chat_id="{self.chat_id}"')
                print(presentInDatabase)
                if not presentInDatabase:
                    self.sent.append(chosenTutStory) #feel like this is no longer necessary?
                    self.log(f'Sending tutorial story to {self.name}')
                    self.log(f'Selected the following tutorial story: {chosenTutStory}')
                    #Responses to user DEPENDING ON THEIR 'LOCATION' in the experience
                    if self.status == 'tut_started':
                        await self.send_msg(f"Here's the first tutorial story for you to listen to:")
                        await self.send_vn(f'tutorialstories/{chosenTutStory}')
                        await self.send_msg(f"""So, having listened to this person's story, what do you think is the rub? Which driving forces underlie the storyteller's experience?\n\nWhen you're ready to send in an audio response to this story, just record and send it to Wisperbot.\n\nRemember to reflect on the which values seems to drive the person in this story but do so through 'active listening': by paraphrasing and asking clarifying questions.\n\nRecord your response whenever you're ready!\n\nP.S. You will only be able to request another tutorial story when you have responded to this one first. (:""")
                        #For logging and status change:
                        match = re.search(r"\d", chosenTutStory) # Extract digit
                        i = match.group(0)
                        self.status = f'tut_story{i}received'
                    elif self.status == 'tut_story1responded':
                        await self.send_msg(f"Here's the second tutorial story for you to listen to, from someone else:")
                        await self.send_vn(f'tutorialstories/{chosenTutStory}')
                        await self.send_msg(f"""Again, have a think about which values seem embedded in this person's story. When you're ready to record your response, go ahead!""")
                        #For logging and status change:
                        match = re.search(r"\d", chosenTutStory) # Extract digit
                        i = match.group(0)
                        self.status = f'tut_story{i}received'
                    elif self.status == 'tut_story2responded':
                        await self.send_msg(f"Here's the third tutorial story for you to listen to:")
                        await self.send_vn(f'tutorialstories/{chosenTutStory}')
                        await self.send_msg(f"""Again, have a think about which values seem embedded in this person's story. When you're ready to record your response, go ahead!""")
                        #For logging and status change:
                        match = re.search(r"\d", chosenTutStory) # Extract digit
                        i = match.group(0)
                        self.status = f'tut_story{i}received'
                    elif self.status == 'tut_story3responded':
                        await self.send_msg(f"Here's the fourth and final tutorial story:")
                        await self.send_vn(f'tutorialstories/{chosenTutStory}')
                        await self.send_msg(f"""Again, have a think about which values seem embedded in this person's story. When you're ready to record your response, go ahead!""")
                        #For logging and status change:
                        match = re.search(r"\d", chosenTutStory) # Extract digit
                        i = match.group(0)
                        self.status = f'tut_story{i}received'
            except IndexError:
                await self.send_msg("""Exciting! You've listened to all the tutorial stories I've got for you!\n\nTime to enter Wisper, where you will also be able to listen to other people stories, and send them a one-time active listening response about the values they seem to balance.\n\nAdditionally, and importantly, you will also get to record your own stories, based on a prompt! Other people will then be able respond to your story, the same way you have responded to theirs.\n\nUse the /endtutorial command to enter the world of Wisper!""")
                #INSTRUCTIONS USEFUL FOR LATER "To request a prompt and record your own story, use the /requestprompt command. To listen to another person's story, use the /request command."
                self.log(f'No other voicenotes to choose from. Tutorial completed.')
                #Need to add a 'tutorialcompleted' variable here that switches to 1 when the user has gone through this, 
                #so we know not to send them any tutorial related stuff anymore (and not to use any tutorial functions).
                self.status = f'tut_completed'

    async def transcribe(self,filename):
        txt = f"{filename}.txt"
        webm = 'temp.webm'
        cmd = f'ffmpeg -i file:"{filename}" -c:a libopus -b:a 64k -af atempo=2 -y {webm}'
        subprocess.check_output(cmd, shell=True)
        audio_file = open(webm,'rb')
        try:
            transcript = openai.Audio.transcribe('whisper-1',audio_file).pop('text')
            with open(txt,"w",encoding="utf-8") as f:
                f.write(transcript)
            self.log(f"Transcribed {filename} to {filename}.txt")
            os.unlink(webm)
            return transcript
        except openai.error.InvalidRequestError:
            return None
        #cmd = f'rclone copy --drive-shared-with-me -P 00_Participants bryankam8@gmail.com:"04_AUDIO PROTOTYPE_June 2023/00_Participants"'
        #subprocess.check_output(cmd, shell=True)

async def chunk_msg(msg):
    chunks = []
    current_chunk = ""
    for word in msg.split(" "):
        if len(current_chunk + word) > 4096:
            chunks.append(current_chunk)
            current_chunk = ""

        current_chunk += word + " "
    if current_chunk != "":
        chunks.append(current_chunk)
    return chunks

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''Given a message, echo it back with their name'''
    chat = await initialize_chat_handler(update, context)
    bot = chat.context.bot
    if 'group' in chat.chat_type: # To inlude both group and supergroup
        await chat.send_msg("This bot is intended for individual chats only. 🥰 Bye for now")
        await bot.leave_chat(chat_id=chat.chat_id)
        chat.log(f'Left group {chat.name}')
        return
    chat.log(f'{update.message.text}')
    await chat.textresponse_chooser()
    
async def get_voicenote(update: Update, context: CallbackContext) -> None:
    chat = await initialize_chat_handler(update,context)
    # Check the current status to determine how to handle the voice note
    if chat.status.startswith('tut_story') and 'received' in chat.status:
        # Handle as a response to a tutorial story
        chat.status = chat.status.replace('received', 'responded')
    elif chat.status == 'awaiting_story_response':
        # Handle as a user's story following a prompt
        chat.status = 'story_response_received'
        # Logic to handle the story voice note
    else:
        chat.log(f"Uncertain how to handle voicenote received in status {chat.status}")
    path = os.path.join(chat.directory, chat.subdir)
    os.makedirs(path, exist_ok=True)
    # get basic info about the voice note file and prepare it for downloading
    new_file = await context.bot.get_file(update.message.voice.file_id)
    # We used to get the now timestamp:
    # ts = datetime.now().strftime("%Y%m%d-%H:%M")
    # But probably better to get the timestamp of the message:
    ts = update.message.date.strftime("%Y%m%d-%H:%M")
    # download the voice note as a file
    filename = f"{ts}-{update.message.from_user.first_name}-{chat.chat_id}-{new_file.file_unique_id}.ogg"
    filepath = os.path.join(path, filename)
    await new_file.download_to_drive(filepath)
    chat.log(f"Downloaded voicenote as {filepath}")
    await chat.send_msg(f"Thank you for recording your response, {chat.first_name}!")
    transcript = await chat.transcribe(filepath)
    # Uncomment the following if you want people to receive their transcripts:
    #chunks = await chunk_msg(f"Transcription:\n\n{transcript}")
    #for chunk in chunks:
    #    await context.bot.send_message(
    #        chat_id=update.effective_chat.id,
    #        text=chunk
    #    )
    await chat.send_tutstory()

async def initialize_chat_handler(update,context=None):
    chat_id = update.effective_chat.id
    if chat_id not in chat_handlers:
        chat_handlers[chat_id] = ChatHandler(chat_id,update,context)
        chat = chat_handlers[chat_id]
    chat = chat_handlers[chat_id]
    return chat

#------- COMMANDS -------#

async def gettutorialstory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''When we receive /start, start logging, say hi, and send voicenote back if it's a DM'''
    chat = await initialize_chat_handler(update,context)
    chat.log('Received /gettutorialstory command')
    # Need to send back in order so will need to track what has been sent to the user
    await chat.send_tutstory()

async def endtutorial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''When we receive /endtutorial, start main sequence'''
    chat = await initialize_chat_handler(update,context)
    chat.status = 'tut_complete'
    chat.subdir = 'story'
    chat.log('Received /endtutorial command')
    # Need to send back in order so will need to track what has been sent to the user
    await chat.send_endtutorial()

async def starttutorial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = await initialize_chat_handler(update,context)
    chat.status = 'tut_started'
    chat.log(f'Chat {chat.chat_id} from {chat.name}: Sending first instructions')
    await chat.send_msg("""Awesome, let's get started! ✨\n\nIn this tutorial, you will get the chance to listen to a max. of 4 stories from other people.\n\nAfter each audio story, think about which values seem to be at play for that person at that time.\n\nAfter you've taken some time to think about the story, please take a minute or two to record a response to this person's story in an 'active listening' way.\n\nThis means that you try repeat back to the person what they said but in your own words, and that you ask clarifying questions that would help the person think about which values seemed to be at odds with one another in this situation. This way of listening ensures that the person you're responding to really feels heard.💜\n\nIn this tutorial, your response will NOT be sent back to the story's author, so don't be afraid to practice! ^^\n\nReady to listen to some stories? Please run /gettutorialstory to receive a practice story to start with.""")

async def help_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''When we receive /help, start logging, say hi, and send voicenote back if it's a DM'''
    chat = await initialize_chat_handler(update,context)
    chat.log('Received /help command')
    await chat.send_msg("""Thanks for submitting your reflections. If you have any trouble, feel free to explain the issue by typing a text message to me below and someone from Bloom will help.""")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''When we receive /start, start logging, say hi, and send voicenote back if it's a DM'''
    chat = await initialize_chat_handler(update,context)
    chat.status = 'start_welcomed'
    chat.log('Received /start command')
    bot = chat.context.bot
    if 'group' in chat.chat_type: # To inlude both group and supergroup
        await bot.leave_chat(chat_id=chat.chat_id)
        chat.log(f'Left group {chat.name}')
        return
    else:
        await chat.send_msg(f"""Hi {chat.first_name}! 👋🏻\n\nWelcome to Wisperbot, which is a bot designed to help you reflect on the values and motivations that are embedded in your life's stories, as well as the stories of others.\n\nIn Wisperbot, you get to share your story with others based on prompts, and you get to reflect on other people's stories by engaging in 'active listening', which we will tell you more about in a little bit.\n\nSince this is your first time using Wisperbot, you are currently in the 'tutorial space' of Wisperbot, where you will practice active listening a couple of times before entering Wisper for real.\n\nReady to practice? Enter /starttutorial for further instructions. 😊""")
    #cmd = f'rclone copy --drive-shared-with-me -P 00_Participants bryankam8@gmail.com:"04_AUDIO PROTOTYPE_June 2023/00_Participants"'
    #subprocess.check_output(cmd, shell=True)

if __name__ == '__main__':
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS logs (
      timestamp INTEGER,
      name TEXT,
      chat_id INTEGER, 
      filename TEXT
    )""")
    application = ApplicationBuilder().token(TOKEN).build()

    echo_handler = MessageHandler(filters.TEXT & (~filters.COMMAND), echo)
    help_handler = CommandHandler('help', help_msg)
    gettutorialstory_handler = CommandHandler('gettutorialstory', gettutorialstory)
    starttutorial_handler = CommandHandler('starttutorial', starttutorial)
    endtutorial_handler = CommandHandler('endtutorial', endtutorial)
    voice_handler = MessageHandler(filters.VOICE , get_voicenote)
    start_handler = CommandHandler('start', start)

    application.add_handler(echo_handler)
    application.add_handler(help_handler)
    application.add_handler(gettutorialstory_handler)
    application.add_handler(starttutorial_handler)
    application.add_handler(endtutorial_handler)
    application.add_handler(voice_handler)
    application.add_handler(start_handler)

    application.run_polling()
