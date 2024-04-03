###---------LIBRARY IMPORTS---------###
import csv
from datetime import datetime
import dotenv
import logging
import os
import sqlite3
import sys
from openai import OpenAI
from telegram.constants import ParseMode
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
    sender TEXT,
    send_id INTEGER, 
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
    def __init__(self, chat_id, update=None, context=None):
        if not update.message or not context:
            missing = "update.message" if not update.message else "context"
            logging.error(f'Received an update without {missing} defined: {update}')
            return
        self.chat_id = chat_id
        self.chat_type = update.message.chat.type
        self.context = context
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
        self.set_paired_user()  

    def set_paired_user(self):
        '''Set the paired user based on the chat's username'''
        self.paired_user = user_pairs.get(self.name, None)
        self.paired_chat_id = name_to_chat_id.get(self.paired_user, None)

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
                # Request succeeded, break the loop
                break
            except TimedOut:
                # Timeout error occured, log a warning
                self.logger.warning("Request timed out, retrying...")

                # Wait a few seconds before retrying
                await asyncio.sleep(5)

    async def send_video(self,FN):
        if VIDEO:
            await self.context.bot.send_video(chat_id=self.chat_id, video=open(FN, 'rb'), caption="Click to start, and make sure your sound is on. 🔊👍🏻", has_spoiler=True, width=1280, height=720)

    async def send_vn(self,VN):
        '''Send a voicenote file'''
        c.execute("INSERT INTO logs VALUES (?,?,?,?,?,?,?,?)", 
          (datetime.now(),
           self.name, 
           self.chat_id, 
           VN,
           filename,
           event,
           status))
        conn.commit()
        self.sent.append(VN)
        await self.context.bot.send_voice(chat_id=self.chat_id, voice=VN)
        self.log(f'Sent {VN}')

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

    async def sqlquery(self,cmd,fetchall=False):
        c.execute(cmd)
        if fetchall:
            result = c.fetchall()
        else:
            result = c.fetchone()
        return result

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

