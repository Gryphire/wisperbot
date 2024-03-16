import csv
import dotenv
import logging
import os
import sys
from telegram import Update
from telegram.ext import (ApplicationBuilder,
    filters,
    CallbackContext,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ApplicationBuilder)
from openai import OpenAI

dotenv.load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN")
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
# These lines set TRANSCRIBE and VIDEO to True if the environment variable is not set to 'false'
TRANSCRIBE = os.environ.get("TRANSCRIBE","").lower() != 'false'
VIDEO = os.environ.get("VIDEO","").lower() != 'false'

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

## USER PAIRS FILE SETUP
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

async def initialize_chat_handler(update,context=None):
    chat_id = update.effective_chat.id
    if chat_id not in chat_handlers:
        chat_handlers[chat_id] = ChatHandler(chat_id,update,context)
        chat = chat_handlers[chat_id]
    chat = chat_handlers[chat_id]
    return chat

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
        if self.paired_user: # Don't log anything if they aren't paired
            self.logger.info(msg)
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

    async def send_msg(self, text, reply_markup = None):
        '''Send a message in this chat; try infinitely'''
        if not self.paired_user:
            return
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

    async def send_video(self,FN):
        if VIDEO:
            await chat.context.bot.send_video(chat_id=chat.chat_id, video=open(FN, 'rb'), caption="Click to start, and make sure your sound is on. 🔊👍🏻", has_spoiler=True, width=1280, height=720)

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

# Define the states for the ConversationHandler
states = ['TUT_STORY1RECEIVED',
'TUT_STORY2RECEIVED',
'TUT_STORY3RECEIVED',
'TUT_STORY4RECEIVED',
'TUT_STORY1RESPONDED',
'TUT_STORY2RESPONDED',
'TUT_STORY3RESPONDED',
'TUT_STORY4RESPONDED',
'TUT_COMPLETED',
'AWAITING_INTRO']
states_range = range(len(states))

# Create variables dynamically based on the position in the states list
for idx, state in enumerate(states):
    exec(f"{state} = {idx}")

# Map the states to the state numbers
for i in state_mapping:
    print(i,state_mapping[i])

def get_state_name(state_number):
    return state_mapping[state_number]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''When we receive /start, start logging, say hi, and send voicenote back if it's a DM'''
    chat = await initialize_chat_handler(update,context)
    if not chat.paired_user:
        await chat.context.bot.send_message(
            chat.chat_id, "Sorry, we don't have a record of your username!", parse_mode='markdown'
        )
        return
    chat.log('Received /start command')
    bot = chat.context.bot
    if 'group' in chat.chat_type: # To inlude both group and supergroup
        await bot.leave_chat(chat_id=chat.chat_id)
        chat.log(f'Left group {chat.name}')
        chat.status = 'left_group'
        return
    else:
        await chat.send_msg(f"""Hi {chat.first_name}! 👋🏻\n\nWelcome to Wisperbot, which is a bot designed to help you reflect on the values and motivations that are embedded in your life's stories, as well as the stories of others.\n\nIn Wisperbot, you get to share your story with others based on prompts, and you get to reflect on other people's stories by engaging in 'curious listening', which we will tell you more about in a little bit.""")
        await chat.send_msg(f"""Since this is your first time using Wisperbot, you are currently in the 'tutorial space' of Wisperbot, where you will practice active listening a couple of times before entering Wisper for real.\n\nHere is a short, animated explainer video we'd like to ask you to watch before continuing.""")
        await chat.send_video('explainer.mp4')
        await chat.send_msg(f"""Once you have watched the video, enter /starttutorial for further instructions. 😊""")
        chat.status = 'start_welcomed'

async def start_tutorial(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_started'
    chat.log(f'Chat {chat.chat_id} from {chat.name}: Sending first instructions')
    await chat.send_msg("""Awesome, let's get started! ✨\n\nIn this tutorial, you will get the chance to listen to a max. of 4 stories from other people.\n\nAfter each audio story, think about which values seem to be at play for that person at that time.\n\nAfter you've taken some time to think about the story, please take a minute or two to record a response to this person's story in an 'active listening' way.\n\nThis means that you try repeat back to the person what they said but in your own words, and that you ask clarifying questions that would help the person think about which values seemed to be at odds with one another in this situation. This way of listening ensures that the person you're responding to really feels heard.💜\n\nIn this tutorial, your response will NOT be sent back to the story's author, so don't be afraid to practice! ^^\n\nReady to listen to some stories? Please run /gettutorialstory to receive a practice story to start with.""")

async def get_tutorial_story(update, context):
    chat = await initialize_chat_handler(update, context)
    current_state = chat.status
    next_state = None

    if next_state:
        chat.status = next_state
        # Logic to send the next tutorial story based on the next_state
    else:
        await chat.send_msg("You have completed all tutorial stories. You can now proceed to the next step.")

async def start_welcomed(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'start_welcomed'

async def tut_story1received(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_story1received'

async def tut_story2received(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_story2received'

async def tut_story3received(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_story3received'

async def tut_story4received(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_story4received'

async def tut_story1responded(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_story1responded'

async def tut_story2responded(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_story2responded'

async def tut_story3responded(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_story3responded'

async def tut_story4responded(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_story4responded'

async def tut_completed(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'tut_completed'

async def awaiting_intro(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'awaiting_intro'

async def cancel(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.status = 'cancel'

if __name__ == '__main__':
    # Define the conversation handler with states and corresponding functions
    # User runs /start and receives start message
    # From status "none" to status "start_welcomed"
    #start_handler = CommandHandler('start', start)
    # User runs /starttutorial and receives tutorial instructions
    # From status "start_welcomed" to "tut_started"
    tutorial_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start),
            CommandHandler('starttutorial', start_tutorial),
            CommandHandler('gettutorialstory', get_tutorial_story),
        ],
        states={
            TUT_STORY1RECEIVED: [MessageHandler(filters.TEXT, tut_story1received)],
            TUT_STORY2RECEIVED: [MessageHandler(filters.TEXT, tut_story2received)],
            TUT_STORY3RECEIVED: [MessageHandler(filters.TEXT, tut_story3received)],
            TUT_STORY4RECEIVED: [MessageHandler(filters.TEXT, tut_story4received)],
            TUT_STORY1RESPONDED: [MessageHandler(filters.TEXT, tut_story1responded)],
            TUT_STORY2RESPONDED: [MessageHandler(filters.TEXT, tut_story2responded)],
            TUT_STORY3RESPONDED: [MessageHandler(filters.TEXT, tut_story3responded)],
            TUT_STORY4RESPONDED: [MessageHandler(filters.TEXT, tut_story4responded)],
            TUT_COMPLETED: [MessageHandler(filters.TEXT, tut_completed)],
            AWAITING_INTRO: [MessageHandler(filters.TEXT, awaiting_intro)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    logging.info(f"OpenAI transcription is {'on' if TRANSCRIBE else 'off'}, video is {'on' if VIDEO else 'off'}")
    application = ApplicationBuilder().token(TOKEN).build()
    #application.add_handler(start_handler)
    application.add_handler(tutorial_handler)
    application.run_polling()