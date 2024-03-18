###---------LIBRARY IMPORTS---------###
import dotenv
import os
import re
from telegram import Update
from telegram.ext import (ApplicationBuilder,
    filters,
    CallbackContext,
    ContextTypes,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ApplicationBuilder)
from chat import ChatHandler

###---------INITIALISING VARIABLES---------###
dotenv.load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN")

###---------CONVERSATION HANDLER SETUP---------###
states = ['START_WELCOMED',
'TUTORIAL_STARTED',
'TUT_STORY1RECEIVED',
'TUT_STORY1RESPONDED',
'TUT_STORY2RECEIVED',
'TUT_STORY2RESPONDED',
'TUT_STORY3RECEIVED',
'TUT_STORY3RESPONDED',
'TUT_STORY4RECEIVED',
'TUT_STORY4RESPONDED',
'TUT_COMPLETED',
'AWAITING_INTRO']
END = ConversationHandler.END
states_range = range(len(states))

# Create variables dynamically based on the position in the states list
for idx, state in enumerate(states):
    exec(f"{state} = {idx}")

state_mapping = dict(zip(states, states_range))

# Map the states to the state numbers
for i in state_mapping:
    print(i,state_mapping[i])

def get_state_name(state_number):
    return state_mapping[state_number]

# Keep a dictionary of loggers:
chat_handlers = {}

# CHECK FOR FOLDER TO KEEP TRACK OF WHICH TUTORIAL STORIES HAVE BEEN SENT TO USER ALREADY
os.makedirs('sent', exist_ok=True)

# List of tutorialstories at very start (when none have been sent yet)
# Note that this variable resets everytime the bot restarts!!
tutorial_files = [f for f in sorted(os.listdir('tutorialstories/')) if f.startswith('tutstory')]

async def initialize_chat_handler(update,context=None):
    chat_id = update.effective_chat.id
    if chat_id not in chat_handlers:
        chat_handlers[chat_id] = ChatHandler(chat_id,update,context)
        chat = chat_handlers[chat_id]
    chat = chat_handlers[chat_id]
    return chat

async def get_voicenote(update: Update, context: CallbackContext) -> None:
    chat = await initialize_chat_handler(update,context)
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
        return

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
        return END
    else:
        await chat.send_msg(f"""Hi {chat.first_name}! 👋🏻\n\nWelcome to Wisperbot, which is a bot designed to help you reflect on the values and motivations that are embedded in your life's stories, as well as the stories of others.\n\nIn Wisperbot, you get to share your story with others based on prompts, and you get to reflect on other people's stories by engaging in 'curious listening', which we will tell you more about in a little bit.""")
        await chat.send_msg(f"""Since this is your first time using Wisperbot, you are currently in the 'tutorial space' of Wisperbot, where you will practice active listening a couple of times before entering Wisper for real.\n\nHere is a short, animated explainer video we'd like to ask you to watch before continuing.""")
        await chat.send_video('explainer.mp4')
        await chat.send_msg(f"""Once you have watched the video, enter /starttutorial for further instructions. 😊""")
        chat.status = 'start_welcomed'
        return START_WELCOMED

async def start_tutorial(update, context):
    '''When we receive /starttutorial, send the first instructions to the user (Tell them to run /gettutorial)'''
    chat = await initialize_chat_handler(update, context)
    if update.message.text == '/starttutorial':
        chat.log('Received /starttutorial command. Sending first instructions')
        await chat.send_msg("""Awesome, let's get started! ✨\n\nIn this tutorial, you will get the chance to listen to a max. of 4 stories from other people.\n\nAfter each audio story, think about which values seem to be at play for that person at that time.\n\nAfter you've taken some time to think about the story, please take a minute or two to record a response to this person's story in an 'active listening' way.\n\nThis means that you try repeat back to the person what they said but in your own words, and that you ask clarifying questions that would help the person think about which values seemed to be at odds with one another in this situation. This way of listening ensures that the person you're responding to really feels heard.💜\n\nIn this tutorial, your response will NOT be sent back to the story's author, so don't be afraid to practice! ^^\n\nReady to listen to some stories? Please run /gettutorialstory to receive a practice story to start with.""")
        chat.status = 'tut_started'
        return TUTORIAL_STARTED
    else:
        await chat.send_msg("""Please run /starttutorial""")


async def get_tutorial_story(update, context):
    '''Run this the first time we receive /gettutorialstory'''
    chat = await initialize_chat_handler(update, context)
    if update.message.text == '/gettutorialstory':
        chat.log('Received /gettutorialstory command')
        await chat.send_msg(f"Here's the first tutorial story for you to listen to:")
        await chat.send_vn(f'tutorialstories/{tutorial_files[0]}')
        await chat.send_msg(f"""So, having listened to this person's story, what do you think is the rub? Which driving forces underlie the storyteller's experience?\n\nWhen you're ready to send in an audio response to this story, just record and send it to Wisperbot.\n\nRemember to reflect on the which values seems to drive the person in this story but do so through 'active listening': by paraphrasing and asking clarifying questions.\n\nRecord your response whenever you're ready!\n\nP.S. You will only be able to request another tutorial story when you have responded to this one first. (:""")
        chat.status = f'tut_story1received'
        return TUT_STORY1RECEIVED
    else:
        await chat.send_msg("""Please run /gettutorialstory""")

async def tut_story1received(update, context):
    chat = await initialize_chat_handler(update, context)
    print(update)
    if update.message.text:
        await chat.send_msg("Please send a voicenote response 😊")
    else:
        get_voicenote(update, context)
        chat.status = 'tut_story1responded'
        return TUT_STORY1RESPONDED

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
    voice_handler = MessageHandler(filters.VOICE, get_voicenote)
    tutorial_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            START_WELCOMED: [MessageHandler(filters.TEXT, start_tutorial)],
            TUTORIAL_STARTED: [MessageHandler(filters.TEXT, get_tutorial_story)],
            TUT_STORY1RECEIVED: [MessageHandler(filters.TEXT, tut_story1received)],
            TUT_STORY1RESPONDED: [MessageHandler(filters.TEXT, tut_story1responded)],
            TUT_STORY2RECEIVED: [MessageHandler(filters.TEXT, tut_story2received)],
            TUT_STORY2RESPONDED: [MessageHandler(filters.TEXT, tut_story2responded)],
            TUT_STORY3RECEIVED: [MessageHandler(filters.TEXT, tut_story3received)],
            TUT_STORY3RESPONDED: [MessageHandler(filters.TEXT, tut_story3responded)],
            TUT_STORY4RECEIVED: [MessageHandler(filters.TEXT, tut_story4received)],
            TUT_STORY4RESPONDED: [MessageHandler(filters.TEXT, tut_story4responded)],
            TUT_COMPLETED: [MessageHandler(filters.TEXT, tut_completed)],
            AWAITING_INTRO: [MessageHandler(filters.TEXT, awaiting_intro)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application = ApplicationBuilder().token(TOKEN).build()
    #application.add_handler(start_handler)
    application.add_handler(tutorial_handler)
    application.add_handler(voice_handler)
    application.run_polling()
