###---------LIBRARY IMPORTS---------###
import csv
from datetime import datetime, timedelta
import dotenv
import json
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

# Create chat_state table for persisting conversation state
c.execute("""CREATE TABLE IF NOT EXISTS chat_state (
    chat_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    week INTEGER DEFAULT 1,
    start_date TEXT,
    sqlite_date TEXT,
    subdir TEXT,
    sent TEXT,
    paired_user TEXT,
    paired_chat_id INTEGER,
    name TEXT,
    first_name TEXT,
    voice_count INTEGER DEFAULT 0,
    week2_start_date TEXT,
    last_updated TEXT
)""")

# Create scheduled_jobs table for persisting scheduled messages
c.execute("""CREATE TABLE IF NOT EXISTS scheduled_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    scheduled_time TEXT NOT NULL,
    message_type TEXT NOT NULL,
    content TEXT,
    status TEXT,
    created_at TEXT NOT NULL,
    completed INTEGER DEFAULT 0,
    FOREIGN KEY (chat_id) REFERENCES chat_state(chat_id)
)""")

conn.commit()

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
                if len(row) < 2:
                    continue  # Skip invalid rows
                user1, user2 = row[0].strip(), row[1].strip()
                if user1 and user2:  # Only add if both usernames are non-empty
                    user_pairs[user1] = user2
                    user_pairs[user2] = user1  # Assuming a two-way relationship for simplicity
        return user_pairs
    except Exception as e:
        print(f"Failed to load user pairs from {filename}: {e}")
        sys.exit(1)

def reload_user_pairs(filename='user_pairs.csv'):
    """Reload user pairs from file and update the global variable"""
    global user_pairs
    try:
        new_pairs = load_user_pairs(filename)
        user_pairs = new_pairs
        logging.info(f"User pairs reloaded successfully. {len(new_pairs)//2} pairs loaded.")
        return True, f"Successfully reloaded {len(new_pairs)//2} user pairs."
    except Exception as e:
        error_msg = f"Failed to reload user pairs: {e}"
        logging.error(error_msg)
        return False, error_msg

def validate_csv_content(csv_content):
    """Validate CSV content and return parsed pairs"""
    try:
        import io
        reader = csv.reader(io.StringIO(csv_content))
        pairs = {}
        for row in reader:
            if len(row) < 2:
                continue
            user1, user2 = row[0].strip(), row[1].strip()
            if not user1 or not user2:
                continue
            if user1 in pairs or user2 in pairs:
                return False, f"Duplicate user found: {user1} or {user2}"
            pairs[user1] = user2
            pairs[user2] = user1
        return True, pairs
    except Exception as e:
        return False, f"CSV validation error: {e}"

def save_user_pairs_from_dict(pairs_dict, filename='user_pairs.csv'):
    """Save user pairs dictionary to CSV file"""
    try:
        # Extract unique pairs (avoid duplicates)
        seen = set()
        pairs_list = []
        for user1, user2 in pairs_dict.items():
            if user1 < user2:  # Normalize order
                pair = (user1, user2)
            else:
                pair = (user2, user1)
            if pair not in seen:
                seen.add(pair)
                pairs_list.append(pair)
        
        with open(filename, 'w', newline='') as csvfile:
            writer = csv.writer(csvfile)
            for user1, user2 in pairs_list:
                writer.writerow([user1, user2])
        
        return True, f"Successfully saved {len(pairs_list)} pairs to {filename}"
    except Exception as e:
        return False, f"Failed to save user pairs: {e}"

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
    def __init__(self, chat_id, update=None, context=None, start_date=None, restore_from_db=False):
        self.chat_id = chat_id
        self.context = context
        self.update = update if update else None
        
        if restore_from_db:
            # Restore from database
            self._restore_from_db()
            # Update context and update if provided
            if context:
                self.context = context
            if update:
                self.update = update
                # Update chat_type and name mapping if we have update info
                if update.message:
                    self.chat_type = update.message.chat.type
                    if self.chat_type == 'private' and update.message.from_user:
                        username = update.message.from_user.username
                        if username:
                            name_to_chat_id[username] = self.chat_id
                            # Update name if it was restored from DB but we have fresh info
                            if not self.name or self.name != username:
                                self.name = username
                            if update.message.from_user.first_name:
                                self.first_name = update.message.from_user.first_name
                    elif 'group' in self.chat_type:
                        self.name = update.message.chat.title if update.message.chat.title else self.name
        else:
            # Normal initialization from update
            if not update or not update.message or not context:
                missing = "update.message" if not update or not update.message else "context"
                logging.error(f'Received an update without {missing} defined: {update}')
                return
            self.chat_type = update.message.chat.type
            self.start_date = datetime.fromisoformat(start_date) if isinstance(start_date, str) else start_date
            if self.start_date is None:
                self.start_date = datetime.now()
            self.sqlite_date = self.start_date.strftime("%Y-%m-%d %H:%M:%S")
            self.sent = []
            # When chat first starts, the user will be in tutorial mode
            self._status = 'none'
            # Initial voicenotes will be saved under "tutorialresponses"
            self.subdir = 'tutorialresponses'
            self.paired_user = None  # Initialize paired_user attribute
            self.voice_count = 0
            self.week = 1
            self.week2_start_date = None
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
            # Save initial state
            self.save_state()
        
        self.logger = self.get_logger()# Set the paired user during initialization
    
    def _restore_from_db(self):
        '''Restore chat handler state from database'''
        try:
            c.execute("SELECT * FROM chat_state WHERE chat_id = ?", (self.chat_id,))
            row = c.fetchone()
            if row:
                # Restore from database
                self._status = row[1]  # status
                self.week = row[2] if row[2] else 1  # week
                self.start_date = datetime.fromisoformat(row[3]) if row[3] else datetime.now()  # start_date
                self.sqlite_date = row[4] if row[4] else self.start_date.strftime("%Y-%m-%d %H:%M:%S")  # sqlite_date
                self.subdir = row[5] if row[5] else 'tutorialresponses'  # subdir
                self.sent = json.loads(row[6]) if row[6] else []  # sent (JSON array)
                self.paired_user = row[7]  # paired_user
                self.paired_chat_id = row[8]  # paired_chat_id
                self.name = row[9]  # name
                self.first_name = row[10] if row[10] else ''  # first_name
                self.voice_count = row[11] if row[11] else 0  # voice_count
                self.week2_start_date = datetime.fromisoformat(row[12]) if row[12] else None  # week2_start_date
                
                # Restore chat_type from name (we'll need to update this when we get an update)
                # For now, assume private if name exists
                self.chat_type = 'private' if self.name else 'group'
                
                logging.info(f"Restored chat state for chat_id {self.chat_id}: status={self._status}, week={self.week}")
            else:
                # No state found, use defaults
                logging.warning(f"No persisted state found for chat_id {self.chat_id}, using defaults")
                self._status = 'none'
                self.week = 1
                self.start_date = datetime.now()
                self.sqlite_date = self.start_date.strftime("%Y-%m-%d %H:%M:%S")
                self.subdir = 'tutorialresponses'
                self.sent = []
                self.paired_user = None
                self.paired_chat_id = None
                self.name = None
                self.first_name = ''
                self.voice_count = 0
                self.week2_start_date = None
                self.chat_type = 'private'
        except Exception as e:
            logging.error(f"Error restoring state for chat_id {self.chat_id}: {e}")
            # Fall back to defaults
            self._status = 'none'
            self.week = 1
            self.start_date = datetime.now()
            self.sqlite_date = self.start_date.strftime("%Y-%m-%d %H:%M:%S")
            self.subdir = 'tutorialresponses'
            self.sent = []
            self.paired_user = None
            self.paired_chat_id = None
            self.name = None
            self.first_name = ''
            self.voice_count = 0
            self.week2_start_date = None
            self.chat_type = 'private'
    
    def save_state(self):
        '''Save current chat handler state to database'''
        try:
            sent_json = json.dumps(self.sent) if self.sent else '[]'
            start_date_str = self.start_date.isoformat() if self.start_date else None
            sqlite_date_str = self.sqlite_date if hasattr(self, 'sqlite_date') and self.sqlite_date else None
            week2_start_str = self.week2_start_date.isoformat() if hasattr(self, 'week2_start_date') and self.week2_start_date else None
            paired_chat_id = getattr(self, 'paired_chat_id', None)
            
            c.execute("""INSERT OR REPLACE INTO chat_state 
                (chat_id, status, week, start_date, sqlite_date, subdir, sent, paired_user, 
                 paired_chat_id, name, first_name, voice_count, week2_start_date, last_updated)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.chat_id, self._status, getattr(self, 'week', 1), start_date_str, sqlite_date_str,
                 self.subdir, sent_json, getattr(self, 'paired_user', None), paired_chat_id, getattr(self, 'name', None),
                 getattr(self, 'first_name', ''), getattr(self, 'voice_count', 0), week2_start_str,
                 datetime.now().isoformat()))
            conn.commit()
        except Exception as e:
            logging.error(f"Error saving state for chat_id {self.chat_id}: {e}")

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
            # Save both chat handlers after pairing
            chat.save_state()
        except KeyError:
            self.log(f'Paired chat id not yet found: {self.paired_user}')
            pass
        # Save state after setting paired user
        self.save_state()

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, value):
        if self.status != value:
            self.log(f'Status changing from "{self.status}" to "{value}"')
            self._status = value
            # Auto-save state when status changes
            self.save_state()

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
            self.save_state()  # Save state after updating sent list
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
        job_id = None
        try:
            update = context.job.data['update']
            VN = context.job.data['VN']
            Text = context.job.data['Text']
            img = context.job.data['img']
            status = context.job.data['status']
            scheduled_time = context.job.data['scheduled_time']
            job_id = context.job.data.get('job_id')
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
        
        # Mark job as completed in database
        if job_id:
            try:
                c.execute("UPDATE scheduled_jobs SET completed = 1 WHERE job_id = ?", (job_id,))
                conn.commit()
                self.log(f"Marked scheduled job {job_id} as completed")
            except Exception as e:
                logging.error(f"Error marking job {job_id} as completed: {e}")
    
    async def schedule(self, send_time, VN, Text, img, status, misfire_grace_time=None):
        self.log(f"Scheduled sending of {VN} and message '{Text}' at {send_time}")
        now = datetime.now()
        delay = (send_time - now).total_seconds()
        
        # Persist job to database
        try:
            # Determine message type and content
            if VN:
                msg_type = 'VN'
                content = VN
            elif Text:
                msg_type = 'Text'
                content = Text
            elif img:
                msg_type = 'img'
                content = img
            else:
                msg_type = 'unknown'
                content = ''
            
            c.execute("""INSERT INTO scheduled_jobs 
                (chat_id, scheduled_time, message_type, content, status, created_at, completed)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (self.chat_id, send_time.isoformat(), msg_type, content, status, 
                 datetime.now().isoformat(), 0))
            conn.commit()
            job_id = c.lastrowid
            self.log(f"Persisted scheduled job {job_id} to database")
        except Exception as e:
            logging.error(f"Error persisting scheduled job for chat_id {self.chat_id}: {e}")
            job_id = None
        
        # Schedule the job in memory
        if self.context and self.context.job_queue:
            self.context.job_queue.run_once(
                self.send_now,
                delay,
                data={'update': self.update, 'VN': VN, 'Text': Text, 'img': img, 'status': status, 
                      'scheduled_time': send_time, 'job_id': job_id},
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
        query = await chat.sqlquery(f"SELECT filename FROM logs WHERE chat_id='{chat.chat_id}' AND event='recv_vn' AND status='{status}' AND datetime(timestamp) >= '{chat.sqlite_date}' ORDER BY timestamp ASC")
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

###---------STATE PERSISTENCE FUNCTIONS---------###
def restore_all_chat_states(chat_handlers_dict):
    '''Restore all chat states from database into chat_handlers dictionary'''
    try:
        c.execute("SELECT chat_id FROM chat_state")
        rows = c.fetchall()
        restored_count = 0
        for row in rows:
            chat_id = row[0]
            if chat_id not in chat_handlers_dict:
                # Create ChatHandler with restore_from_db=True
                chat_handlers_dict[chat_id] = ChatHandler(chat_id, restore_from_db=True)
                restored_count += 1
                logging.info(f"Restored chat handler for chat_id {chat_id}")
        logging.info(f"Restored {restored_count} chat handlers from database")
        return restored_count
    except Exception as e:
        logging.error(f"Error restoring chat states: {e}")
        return 0

async def reschedule_pending_jobs(application, chat_handlers_dict):
    '''Reschedule all pending jobs from database'''
    try:
        now = datetime.now()
        c.execute("""SELECT job_id, chat_id, scheduled_time, message_type, content, status 
                     FROM scheduled_jobs 
                     WHERE completed = 0 AND scheduled_time > ?""", (now.isoformat(),))
        rows = c.fetchall()
        rescheduled_count = 0
        
        for row in rows:
            job_id, chat_id, scheduled_time_str, msg_type, content, status = row
            scheduled_time = datetime.fromisoformat(scheduled_time_str)
            
            # Check if chat handler exists
            if chat_id not in chat_handlers_dict:
                logging.warning(f"Chat handler {chat_id} not found for job {job_id}, skipping")
                continue
            
            chat = chat_handlers_dict[chat_id]
            
            # Need context to schedule jobs - will be set when user sends next message
            # For now, we'll store the job info and reschedule when context is available
            if not hasattr(chat, 'context') or not chat.context:
                logging.warning(f"Chat handler {chat_id} has no context yet, job will be rescheduled on next message")
                # Store job info in chat handler for later rescheduling
                if not hasattr(chat, 'pending_jobs'):
                    chat.pending_jobs = []
                chat.pending_jobs.append({
                    'job_id': job_id,
                    'scheduled_time': scheduled_time,
                    'message_type': msg_type,
                    'content': content,
                    'status': status
                })
                continue
            
            # Reschedule the job
            delay = (scheduled_time - now).total_seconds()
            if delay > 0:
                # Prepare data based on message type
                job_data = {
                    'update': chat.update,
                    'status': status,
                    'scheduled_time': scheduled_time,
                    'job_id': job_id
                }
                if msg_type == 'VN':
                    job_data['VN'] = content
                elif msg_type == 'Text':
                    job_data['Text'] = content
                elif msg_type == 'img':
                    job_data['img'] = content
                
                chat.context.job_queue.run_once(
                    chat.send_now,
                    delay,
                    data=job_data
                )
                rescheduled_count += 1
                logging.info(f"Rescheduled job {job_id} for chat_id {chat_id} at {scheduled_time}")
            else:
                # Job is in the past, mark as completed
                c.execute("UPDATE scheduled_jobs SET completed = 1 WHERE job_id = ?", (job_id,))
                conn.commit()
                logging.warning(f"Job {job_id} was scheduled in the past, marked as completed")
        
        conn.commit()
        logging.info(f"Rescheduled {rescheduled_count} pending jobs")
        return rescheduled_count
    except Exception as e:
        logging.error(f"Error rescheduling pending jobs: {e}")
        return 0

async def reschedule_pending_jobs_for_chat(chat):
    '''Reschedule pending jobs for a specific chat handler when context becomes available'''
    if not hasattr(chat, 'pending_jobs') or not chat.pending_jobs:
        return
    
    if not chat.context or not chat.context.job_queue:
        return
    
    now = datetime.now()
    rescheduled = []
    
    for job_info in chat.pending_jobs[:]:  # Copy list to iterate safely
        scheduled_time = job_info['scheduled_time']
        delay = (scheduled_time - now).total_seconds()
        
        if delay > 0:
            # Prepare job data
            job_data = {
                'update': chat.update,
                'status': job_info['status'],
                'scheduled_time': scheduled_time,
                'job_id': job_info['job_id']
            }
            if job_info['message_type'] == 'VN':
                job_data['VN'] = job_info['content']
            elif job_info['message_type'] == 'Text':
                job_data['Text'] = job_info['content']
            elif job_info['message_type'] == 'img':
                job_data['img'] = job_info['content']
            
            chat.context.job_queue.run_once(
                chat.send_now,
                delay,
                data=job_data
            )
            rescheduled.append(job_info)
            chat.log(f"Rescheduled pending job {job_info['job_id']}")
        else:
            # Job is in the past, mark as completed
            try:
                c.execute("UPDATE scheduled_jobs SET completed = 1 WHERE job_id = ?", (job_info['job_id'],))
                conn.commit()
            except Exception as e:
                logging.error(f"Error marking job {job_info['job_id']} as completed: {e}")
        
        # Remove from pending list
        chat.pending_jobs.remove(job_info)
    
    if rescheduled:
        logging.info(f"Rescheduled {len(rescheduled)} pending jobs for chat_id {chat.chat_id}")
