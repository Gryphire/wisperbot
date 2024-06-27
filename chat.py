###---------LIBRARY IMPORTS---------###
import csv
from datetime import datetime, timedelta
import dotenv
import logging
import os
import sqlite3
import sys
import asyncio
import random
from openai import OpenAI
from telegram.constants import ParseMode
from telegram.error import TimedOut
import subprocess
import atexit

###---------INITIALISING NECESSARY VARS---------###
DB = "wisper.db"
dotenv.load_dotenv()
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
# These lines below set TRANSCRIBE and VIDEO to True if the environment variable is not set to 'false'
TRANSCRIBE = os.environ.get("TRANSCRIBE","").lower() != 'false'
VIDEO = os.environ.get("VIDEO","").lower() != 'false'

###---------SQLITE DATABASE SETUP---------###
conn = sqlite3.connect(DB)
c = conn.cursor()
c.execute("""CREATE TABLE IF NOT EXISTS logs ( timestamp INTEGER,
    chat_id INTEGER, 
    sender TEXT,
    recver TEXT,
    recv_id INTEGER, 
    event TEXT,
    filename TEXT,
    status TEXT
)""")

###---------USER PAIRS FILE SETUP---------###
# Load user pairs from CSV file
def load_user_pairs(filename):
    if not os.path.exists(filename):
        print(f"Error: The file {filename} does not exist.")
        sys.exit(1)
    try:
        user_pairs = {}
        with open(filename, newline='') as csvfile:
            reader = csv.reader(csvfile)
            for row in reader:
                user1, user2 = row
                user_pairs[user1] = user2
                user_pairs[user2] = user1  # Assuming a two-way relationship for simplicity
        return user_pairs
    except Exception as e:
        print(f"Failed to load user pairs from {filename}: {e}")
        sys.exit(1)

user_pairs = load_user_pairs('user_pairs.csv')
name_to_chat_id = {}

###---------LOGGING SETUP---------###
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

logging.info(f"OpenAI transcription is {'on' if TRANSCRIBE else 'off'}, video is {'on' if VIDEO else 'off'}")

###---------SETTING UP CHATHANDLER CLASS---------###
class ChatHandler:
    def __init__(self, chat_id, update=None, context=None, start_date=None):
        if not update.message or not context:
            missing = "update.message" if not update.message else "context"
            logging.error(f'Received an update without {missing} defined: {update}')
            return
        self.chat_id = chat_id
        self.chat_type = update.message.chat.type
        self.context = context
        self.start_date = datetime.fromisoformat(start_date) if isinstance(start_date, str) else start_date
        self.sqlite_date = self.start_date.strftime("%Y-%m-%d %H:%M:%S")
        self.sent = []
        # When chat first starts, the user will be in tutorial mode
        self._status = 'none'
        # Initial voicenotes will be saved under "tutorialresponses"
        self.subdir = 'tutorialresponses'
        self.paired_user = None  # Initialize paired_user attribute
        if self.chat_type == 'private':
            self.name = update.message.from_user.username # This will probably break if username not defined
            self.first_name = update.message.from_user.first_name
            self.first_name = update.message.from_user.full_name
        elif 'group' in self.chat_type:  # To include both group and supergroup
            self.name = update.message.chat.title if update else None
        name_to_chat_id[self.name] = self.chat_id
        try:
            with open(f'chat_sessions/chat-{self.chat_id}', 'r', encoding='utf-8') as f:
                self.number = int(f.read())
        except FileNotFoundError:
            self.number = None
        self.logger = self.get_logger()# Set the paired user during initialization

    def set_paired_user(self, chat_handlers):
        '''Set the paired user based on the chat's username'''
        self.paired_user = user_pairs.get(self.name, None)
        self.log(f'Paired user set to {self.paired_user}')
        self.paired_chat_id = name_to_chat_id.get(self.paired_user, None)
        self.log(f'Paired user id is {self.paired_chat_id}')
        try:
            chat = chat_handlers[self.paired_chat_id]
            chat.paired_user = self.name
            chat.log(f'Paired user set to {self.name}')
            chat.paired_chat_id = self.chat_id
            chat.log(f'Paired user id is {self.chat_id}')
        except KeyError:
            self.log(f'Paired chat id not yet found: {self.paired_user}')
            pass

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        if self.status != value:
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
        if self.paired_user: # Don't log anything if they aren't paired
            self.logger.info(f"{self.name}: {msg}")
            top_level_logger.info(f"chat-{self.chat_id} {self.name}: {msg}")
        #else:
        #    self.logger.info(f'Not logging anything because {self.name} is not paired')
        #Uncomment the above if you want to show that the bot is not logging anything because the user is not paired

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

    async def send_msg(self, text, parse_mode=ParseMode.MARKDOWN, reply_markup = None):
        '''Send a message in this chat; try infinitely'''
        if not self.paired_user:
            return
        while True:
            try:
                # Make the Telegram request
                await self.context.bot.send_message(
                    self.chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup
                )
                self.log_send_text(text=text)
                # Request succeeded, break the loop
                break
            except TimedOut:
                # Timeout error occured, log a warning
                self.logger.warning("Request timed out, retrying...")

                # Wait a few seconds before retrying
                await asyncio.sleep(5)
    
    async def send_msgs(self, messages, send_time):
        await self.send_msg(f"Next prompt will be sent at {send_time}")
        for msg in messages:
            if msg.startswith('img:'):
                img = ':'.join(msg.split(':')[1:])
                send_time = send_time + timedelta(seconds=1)
                await self.send(send_time=send_time, img=img)
            elif msg.startswith('audio:'):
                audio = ':'.join(msg.split(':')[1:])
                send_time = send_time + timedelta(seconds=1)
                await self.send(send_time=send_time, VN=audio)
            else:
                send_time = send_time + timedelta(seconds=1)
                await self.send(send_time=send_time, Text=msg)

    async def send_video(self,FN):
        '''Send a video file, try infinitely'''
        if VIDEO:
            while True:
                try:
                    await self.context.bot.send_video(chat_id=self.chat_id, video=open(FN, 'rb'), caption="Click to start, and make sure your sound is on. 🔊👍🏻", has_spoiler=True, width=1280, height=720)
                    self.log_send_video(FN)
                    break
                except TimedOut:
                    self.logger.warning("Request timed out, retrying...")
                    await asyncio.sleep(5)
    
    async def send_vn(self, VN):
        '''Send a voicenote file, try infinitely'''
        while True:
            try:
                await self.context.bot.send_voice(chat_id=self.chat_id, voice=VN)
                self.log_send_vn(VN)
                break
            except TimedOut:
                self.logger.warning("Request timed out, retrying...")
                await asyncio.sleep(5)

    async def send_img(self, img):
        '''Send an image file, try infinitely'''
        while True:
            try:
                await self.context.bot.send_photo(chat_id=self.chat_id, photo=img)
                self.log_send_img(img)
                break
            except TimedOut:
                self.logger.warning("Request timed out, retrying...")
                await asyncio.sleep(5)

    def log_event(self, sender='', recver='', recv_id='', event='', filename=''):
        c.execute("INSERT INTO logs VALUES (?,?,?,?,?,?,?,?)", 
          (datetime.now(), # timestamp
           self.chat_id, # chat_id
           sender, # sender
           recver, # recver
           recv_id, # recv_id
           event, # event
           filename, # filename
           self.status)) # status
        conn.commit()

    def log_recv_text(self, text):
        self.log(f'bot received: {text}')
        self.log_event(sender=self.name,recver='bot',event=f'Received text: {text}')

    def log_send_text(self, text):
        self.log(f'bot sent: {text}')
        self.log_event(sender='bot',recver=self.name,event=f'Sent text: {text}')

    def log_recv_vn(self, filename):
        self.log(f"Downloaded voicenote as {filename}")
        self.log_event(sender=self.name,recver='bot',event='recv_vn',filename=filename)

    def log_send_vn(self, filename):
        self.log(f"Sent voicenote {filename}")
        self.log_event(sender='bot',recver=self.name,event='send_vn',filename=filename)
    
    def log_send_img(self, filename):
        self.log(f"Sent image {filename}")
        self.log_event(sender='bot',recver=self.name,event='send_img',filename=filename)
    
    def log_send_video(self, filename):
        self.log(f"Sent video {filename}")
        self.log_event(sender='bot',recver=self.name,event='send_video',filename=filename)

    # I think we should have function send(send_time, VN, TEXT)
    # this will call either send_msg or send_vn
    async def send_now(self, context=None, VN=None, Text=None, img=None, status=None):
        '''Send a voicenote file or message, either scheduled or now'''
        try:
            update = context.job.data['update']
            VN = context.job.data['VN']
            Text = context.job.data['Text']
            img = context.job.data['img']
            status = context.job.data['status']
            scheduled_time = context.job.data['scheduled_time']
            # Check if the current time is significantly past the scheduled time
            now = datetime.now()
            if scheduled_time and (now - scheduled_time).total_seconds() > 5:
                self.log(f"Missed scheduled time by {(now - scheduled_time).total_seconds()} seconds. Sending now.")
        except AttributeError:
            pass

        if status:
            self.status = status
        if Text:
            await self.send_msg(Text)
        if VN:
            await self.send_vn(VN)
        if img:
            await self.send_img(img)
    
    async def schedule(self, send_time, VN, Text, img, status, misfire_grace_time=None):
        self.log(f"Scheduled sending of {VN} and message '{Text}' at {send_time}")
        now = datetime.now()
        delay = (send_time - now).total_seconds()
        self.context.job_queue.run_once(
            self.send_now,
            delay,
            data={'update': self.update, 'VN': VN, 'Text': Text, 'img': img, 'status': status, 'scheduled_time': send_time},
            job_kwargs={'misfire_grace_time': misfire_grace_time}
        )
    
    async def send(self, send_time=None, VN=None, Text=None, img=None, status=None):
        now = datetime.now()
        # If send_time is None or in the past, send immediately
        if not send_time or now > send_time:
            await self.send_now(VN=VN,Text=Text,img=img)
            if status:
                self.status = status
        else:
            # Otherwise, schedule the send
            await self.schedule(send_time=send_time,VN=VN,Text=Text,img=img,status=status)

    async def exchange_vns(self, paired_chat, status, Text):
        chat = self
        for c, oc in [(chat, paired_chat), (paired_chat, chat)]:
            query = await c.sqlquery(f"""
                SELECT filename 
                FROM logs 
                WHERE chat_id = '{oc.chat_id}' 
                AND event = 'recv_vn' 
                AND status = '{status}' 
                AND datetime(timestamp) >= '{self.sqlite_date}'
                ORDER BY timestamp ASC
            """)
            
            if query:
                for row in query:
                    file = row[0]
                    c.log(f'Trying to send {file}')
                    await c.send(send_time=datetime.now(), VN=file, Text=f'{Text} Here is your message from {oc.first_name}:')
            else:
                c.log(f'No voice notes found for chat_id {oc.chat_id} with status {status}')
    
    async def get_audio(self, status):
        chat = self
        files = []
        query = await chat.sqlquery(f"SELECT filename FROM logs WHERE chat_id='{chat.chat_id}' AND event='recv_vn' AND status='{status}' AND datetime(timestamp) >= '{chat.sqlite_date}' ORDER BY timestamp DESC")
        for row in query:
            file = row[0]
            chat.log(f'{status} audio file is {file}')
            files.append(file)
        return files
   

    async def transcribe(self,filename):
        if not TRANSCRIBE:
            return None
        txt = f"{filename}.txt"
        webm = 'temp.webm'
        cmd = f'ffmpeg -i file:"{filename}" -c:a libopus -b:a 64k -af atempo=2 -y {webm}'
        subprocess.check_output(cmd, shell=True)
        audio_file = open(webm,'rb')
        try:
            transcript = client.audio.transcriptions.create(model='whisper-1',file=audio_file)
            with open(txt,"w",encoding="utf-8") as f:
                f.write(transcript.text)
            self.log(f"Transcribed {filename} to {filename}.txt")
            os.unlink(webm)
            return transcript
        except Exception as e:
            self.log(f"Transcription error: {type(e).__name__}, {e}")
            return None
        #cmd = f'rclone copy --drive-shared-with-me -P 00_Participants bryankam8@gmail.com:"04_AUDIO PROTOTYPE_June 2023/00_Participants"'
        #subprocess.check_output(cmd, shell=True)

    async def sqlquery(self,cmd,fetchall=True):  # Changed default to True
        c.execute(cmd)
        if fetchall:
            result = c.fetchall()
        else:
            result = c.fetchone()
        return result or []  # Return an empty list if result is None

# Function to dump the logs to a CSV file
def dump_logs_to_csv():
    # Fetch all rows from the logs table
    c.execute("SELECT * FROM logs")
    rows = c.fetchall()

    # Write the rows to a CSV file
    with open('logs.csv', 'w', newline='') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow([x[0] for x in c.description])  # write headers
        writer.writerows(rows)

# Register the function to be called when the program is exiting
atexit.register(dump_logs_to_csv)
