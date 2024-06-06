###---------LIBRARY IMPORTS---------###
from datetime import datetime, timedelta
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
    ApplicationBuilder,
    JobQueue)
from chat import ChatHandler
from telegram.constants import ParseMode

###---------INITIALISING VARIABLES---------###
dotenv.load_dotenv()
TOKEN = os.environ.get("TELEGRAM_TOKEN")
STARTING_STATUS = os.environ.get("STARTING_STATUS")
try:
    START_DATE = eval(os.environ.get("START_DATE"))
except TypeError:
    START_DATE = datetime.now()
    print('Warning! START_DATE not set to a datetime(YYYY,MM,DD,HH,MM) value in .env file. Using now as START_DATE')
try:
    INTERVAL = eval(os.environ.get("INTERVAL"))
except TypeError:
    INTERVAL = timedelta(hours=24)

ORIGINAL_START_DATE = START_DATE
print(f'START_DATE is {START_DATE} and one "day" is {INTERVAL}')

###---------CONVERSATION HANDLER SETUP---------###
states = ['START_WELCOMED',
'TUTORIAL_STARTED',
'TUT_STORY1',
'TUT_STORY2',
'TUT_COMPLETED',
'AWAITING_INTRO',
'WEEK1_PROMPT',
'WEEK1_VT',
'WEEK1_PS',
'WEEK1_FEEDBACK',
'WEEK2_PROMPT',
'WEEK2_VT',
'WEEK2_PS',
'WEEK2_FEEDBACK']

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
    chat.update = update
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
    await chat.send_msg(f"Thank you for recording your response, {chat.first_name}!")
    transcript = await chat.transcribe(filepath)
    chat.log_recv_vn(filename=filepath)
    # Uncomment the following if you want people to receive their transcripts:
    #chunks = await chunk_msg(f"Transcription:\n\n{transcript}")
    #for chunk in chunks:
    #    await context.bot.send_message(
    #        chat_id=update.effective_chat.id,
    #        text=chunk
    #    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    '''When we receive /start, start logging, say hi, and send voicenote back if it's a DM'''
    chat = await initialize_chat_handler(update,context)
    chat.set_paired_user(chat_handlers)
    if not chat.paired_user:
        await chat.context.bot.send_message(
            chat.chat_id, "Sorry, we don't have a record of your username!", parse_mode='markdown'
        )
        return END
    bot = chat.context.bot
    chat.log_recv_text('/start command')
    chat.week = 1
    if STARTING_STATUS:
        chat.log(f'Skipping to {STARTING_STATUS}')
        await chat.send_msg(f'Based on bot\'s .env file, skipping to {STARTING_STATUS}', parse_mode=ParseMode.HTML)
        return state_mapping[STARTING_STATUS]
    if 'group' in chat.chat_type: # To inlude both group and supergroup
        await bot.leave_chat(chat_id=chat.chat_id)
        chat.log(f'Left group {chat.name}')
        chat.status = 'left_group'
        return END
    else:
        await chat.send_msg(f"""Hi {chat.first_name}! 👋🏻\n\nWelcome to Echobot, which is a bot designed to help you reflect on the values and motivations that are embedded in your life's stories, as well as the stories of others.\n\nIn Echobot, you get to share your story with others based on prompts, and you get to reflect on other people's stories by engaging in 'curious listening', which we will tell you more about in a little bit.""")
        await chat.send_msg(f"""Since this is your first time using Echobot, you are currently in the 'tutorial space' of Echobot, where you will practice active listening a couple of times before entering Echo for real.\n\nHere is a short, animated explainer video we'd like to ask you to watch before continuing.""")
        await chat.send_video('explainer.mp4')
        await chat.send_msg(f"""Once you have watched the video, enter /starttutorial for further instructions. 😊""")
        chat.status = 'start_welcomed'
        return START_WELCOMED

async def start_tutorial(update, context):
    '''When we receive /starttutorial, send the first instructions to the user (Tell them to run /gettutorial)'''
    chat = await initialize_chat_handler(update, context)
    if update.message.text == '/starttutorial':
        await chat.send_msg("""Awesome, let's get started! ✨\n\nIn this tutorial, you will get the chance to two personal stories from other people.\n\nAfter each audio story, think about which values seem to be at play for that person at that time.\n\nAfter you've taken some time to think about the story, please take a minute or two to record a response to this person's story in an 'active listening' way.\n\nThis means that you try repeat back to the person what they said but in your own words, and that you ask clarifying questions that would help the person think about which values seemed to be at odds with one another in this situation. This way of listening ensures that the person you're responding to really feels heard.💜\n\nIn this tutorial, your response will NOT be sent back to the story's author, so don't be afraid to practice! ^^\n\nReady to listen to some stories? Please run /gettutorialstory to receive a practice story to start with.""")
        chat.status = 'tut_started'
        chat.log_recv_text('Received /starttutorial')
        return TUTORIAL_STARTED
    else:
        chat.log_recv_text(text=update.message.text)
        await chat.send_msg("""Please run /starttutorial""")

async def get_tutorial_story(update, context):
    '''Run this the first time we receive /gettutorialstory'''
    chat = await initialize_chat_handler(update, context)
    if update.message.text == '/gettutorialstory':
        await chat.send_msg(f"Here's the first tutorial story for you to listen to:")
        await chat.send_vn(VN=f'tutorialstories/{tutorial_files[0]}')
        await chat.send_msg(f"""So, having listened to this person's story, what do you think is the rub? Which driving forces underlie the storyteller's experience?\n\nWhen you're ready to send in an audio response to this story, just record and send it to Echobot.\n\nRemember to reflect on the which values seems to drive the person in this story but do so through 'active listening': by paraphrasing and asking clarifying questions.\n\nRecord your response whenever you're ready!\n\nP.S. You will only be able to request another tutorial story when you have responded to this one first. (:""")
        chat.status = f'tut_story{chat.week}received'
        chat.log_recv_text('Received /gettutorialstory')
        return TUT_STORY1
    else:
        chat.log_recv_text(text=update.message.text)
        await chat.send_msg("""Please run /gettutorialstory""")

async def tut_story1(update, context):
    '''The user is supposed to send a response at this point; if not, ask them to'''
    chat = await initialize_chat_handler(update, context)
    if update.message.text:
        await chat.send_msg("Please send a voicenote response 😊")
    else:
        await get_voicenote(update, context)
        chat.status = f'tut_story{chat.week}responded'
        await chat.send_msg(f"Here's the second tutorial story for you to listen to, from someone else:")
        await chat.send_vn(VN=f'tutorialstories/{tutorial_files[1]}')
        await chat.send_msg(f"""Again, have a think about which values seem embedded in this person's story. When you're ready to record your response, go ahead!""")
        chat.status = f'tut_story2received'
        return TUT_STORY2

async def tut_story2(update, context):
    chat = await initialize_chat_handler(update, context)
    if update.message.text:
        await chat.send_msg("Please send a voicenote response 😊")
    else:
        chat.status = 'tut_story2responded'
        await get_voicenote(update, context)
        await chat.send_msg("""Exciting! You've listened to all the tutorial stories I've got for you!\n\nTime to enter the main EchoBot experience, where you will also be able to listen to other people stories, and send them a one-time 'curious listening' response about the values they seem to balance.\n\nAdditionally, and importantly, you will also get to record your own stories, based on a prompt! Other people will then be able respond to your story, the same way you have responded to theirs.\n\nUse the /endtutorial command to enter the world of EchoBot!""")
        return TUT_COMPLETED

async def tut_completed(update, context):
    chat = await initialize_chat_handler(update, context)
    if update.message.text == '/endtutorial':
        chat.status = 'tut_completed'
        await chat.send_msg(f"Now that the tutorial part of EchoBot has been completed, we think it's nice for both you and your partner to introduce yourselves shortly to one another.\n\nFeel free to include your first name if you want to, but you are not required to do so.\n\nPlease send a voicenote introducing yourself to your partner today. Once both you and your partner have done so, you will be able to continue on to the main EchoBot experience tomorrow.")
        chat.subdir = 'intros'
        chat.status = 'awaiting_intro'
        return AWAITING_INTRO
    else:
        await chat.send_msg("Please run /endtutorial")

async def awaiting_intro(update, context):
    '''Await introductions. Once both are received, exchange them between users, then schedule the first prompt.'''
    chat = await initialize_chat_handler(update, context)
    if update.message.text:
        await chat.send_msg(f"Please send a voicenote introducing yourself to your partner!")
    else: # If it's a voicenote...
        chat.status = 'received_intro'
        await get_voicenote(update, context)
        # Check the database to see if the other user has sent in their introduction
        if not chat.paired_chat_id:
            await chat.send_msg(f"Your partner has not yet sent their introduction. You'll receive it as soon as they send it in!")
            return WEEK1_PROMPT
        else:
            paired_chat = chat_handlers[chat.paired_chat_id]

            if paired_chat.status == 'received_intro':
                await chat.exchange_vns(paired_chat, status='received_intro', Text=f"Your partner has also sent in their introduction!")
            else:
                await chat.send_msg(f"Your partner has not yet sent their introduction. You'll receive it as soon as they send it in!")
                return WEEK1_PROMPT

            send_time = START_DATE + INTERVAL
            for c in (chat,paired_chat):
                c.status = 'intros_complete'
                await c.send_msg(f"Day 1 complete!")
                messages = [
                    "Welcome to the main Echo experience! You have successfully completed on-boarding, and have been already introduced to your Echo partner.",
                    "Over the course of this week, you will be exchanging audio messages and listening to one another. Today, we start with reflecting on your life and record an audio story for your partner. You will do so based on a prompt that you and your partner both will receive in a moment.",
                    "Your personal story prompt is 'What was an experience in your life where you had to handle a complex situation?'",
                    "Take your time to think about this prompt, and submit your audio when you are ready. Don't worry too much about what your Echo partner might think—Echo is also about being compassionate, to yourself and others. Rest assured that you will be met with compassion.",
                    "Make sure you send in your story today, your partner will be doing the same."
                ]
                await c.send_msgs(messages, send_time)
                c.status = f'awaiting_week{chat.week}_prompt'
            return WEEK1_PROMPT

async def handle_prompt(update, context):
    '''Await responses to scheduled week 1 prompt 1'''
    chat = await initialize_chat_handler(update, context)
    chat.subdir = f'week{chat.week}'
    if update.message.text :
        if context.job_queue.jobs():
            await chat.send_msg("Please wait until we send you the next prompt :)")
        else:
            await chat.send_msg("Please send a story response to the above prompt")
    else:
        chat.status = f'received_week{chat.week}_story'
        await get_voicenote(update, context)
        await chat.send_msg(f"Thank you for submitting your audio story, {chat.first_name}! Your story has been saved, but will not yet be sent to your partner. Before that happens, you will be asked to complete one more part tomorrow.")
        await chat.send_msg("Stay tuned, you will continue the Echo journey tomorrow morning and receive further instructions then!")
        paired_chat = chat_handlers[chat.paired_chat_id]
        print(paired_chat.status)
        if paired_chat.status == f'received_week{chat.week}_story':
            send_time = START_DATE + (INTERVAL  * 2)
            for c in (chat,paired_chat):
                c.status = f'week{chat.week}_day2_complete'
                await c.send_msg(f"Hi there! Your partner has now also completed their part of today's assignment.")
                messages = [
                    "Welcome back! Yesterday you recorded a personal story. Today, we would like you to listen to your own story, and think about whether and how you have had to balance between different values. This balancing is what we call 'value tensions'.",
                    "Here are some examples of value tensions.",
                    'img:vt.png',
                    "In today's part of the Echo experience, we would like you to reflect on how you would place yourself on **one or two** of the following value tensions, and send this to your Echo partner in an audio message. Note that you do not need to cover all of these tension; just pick one or two that seem relevant to the story that you recorded earlier. When you are read to record, go ahead." 
                ]
                await c.send_msgs(messages, send_time)
                c.status = f'awaiting_week{chat.week}_vt'
        else:
            await chat.send_msg(f"Your partner has not yet sent their story. I'll let you know as soon as they do!")
        return WEEK1_VT

async def handle_vt(update, context):
    '''Handle the response for week 1 value tension reflection'''
    chat = await initialize_chat_handler(update, context)
    if update.message.voice:
        chat.status = f'received_week{chat.week}_vt'
        await get_voicenote(update, context)
        await chat.send_msg(f"Thank you for sending in your value tension reflection, {chat.first_name}!")
        await chat.send_msg("Now that you have reflected on your life and the value tensions therein, you and your Echo partner will both receive each other's stories tomorrow morning.")
        await chat.send_msg("Stay tuned, you will continue the Echo journey in the coming days.")
        paired_chat = chat_handlers[chat.paired_chat_id]
        if paired_chat.status == f'received_week{chat.week}_vt':
            send_time = START_DATE + (INTERVAL * 3)
            for c, oc in [(chat,paired_chat),(paired_chat,chat)]:
                c.status = 'day3_complete'
                await c.send_msg(f"Hi there. Just a heads up that your partner has now also sent in their reflection. Stay tuned for the next steps tomorrow!")
                #await c.send_msg(f"Day 3 complete!")
                messages = [
                    "Hi there! Your partner and you both have recorded your story and value tension reflections. Time to listen to your partner's audio!",
                    "Here is your partner's initial personal story.",
                    f"audio:{await oc.get_audio('received_week1_story')}",
                    "Here is your partner's value tension reflection on that story.",
                    f"audio:{await oc.get_audio('received_week1_vt')}",
                    "Now, it's important that you listen to these stories as you would to a good friend. Echo is all about 'curious listening', which means that we listen to understand. After having listened to your partner's story and value tension reflection, make sure you try to paraphrase your partner's story in your own words, and ask clarifying questions. That way, your partner will truly feel heard!",
                    "Go ahead and record your 'curious listening' response to your partner's stories when you are ready. Make sure you do so before the end of tomorrow."
                ]
                await c.send_msgs(messages, send_time)
                c.status = 'awaiting_listening_response'
        else:
            await chat.send_msg(f"Your partner has not yet completed their reflection. I'll let you know as soon as they do!")
        return WEEK1_PS
    else:
        await chat.send_msg("Please send a voice note response to the value tension reflection prompt.")

async def handle_ps(update, context):
    chat = await initialize_chat_handler(update, context)#
    if update.message.voice:
        chat.status = f'received_week{chat.week}_ps'
        await get_voicenote(update, context)
        paired_chat = chat_handlers[chat.paired_chat_id]
        await chat.send_msg(f"Thank you for recording your response! I'm sure that your partner {paired_chat.first_name if paired_chat.first_name else ''} will be grateful to hear your attentive perspective!")
        await chat.send_msg("Stay tuned and keep an eye out for next steps from me in the coming days!")
        paired_chat = chat_handlers[chat.paired_chat_id]
        if paired_chat.status == f'received_week{chat.week}_ps':
            send_time = START_DATE + (INTERVAL  * 5)
            for c, oc in [(chat, paired_chat), (paired_chat, chat)]:
                c.status = 'day4_complete'
                await c.send_msg(f"Hi! Just a heads up; your partner has also sent in their 'curious listening' response.")
                #await c.send_msg(f"Day 4 complete!")
                messages = [
                    "Hi there! Yesterday you responded to your partner's stories and lent them your 'curious listening' ear! In the meantime, your partner has also listened to your audio messages. Time to take a listen!",
                    f"audio:{await oc.get_audio('received_week1_ps')}",
                    "When you have listened to their perspective, take a moment to think about your partner's audio message. Do you agree with them? Does their take on your voice messages change anything about how you view your own story?",
                    "Take a moment to reflect on your partner's response and record a final response for them. You might thank them for listening to your stories, or for providing an interesting new perspective. Feel free to share your thoughts and exchange feelings. Your partner will be doing the same for you. Go ahead and record your final reaction when you are ready."
                ]
                await c.send_msgs(messages, send_time)
                c.status = 'awaiting_week1_feedback'
        else:
            await chat.send_msg(f"Your partner has not yet sent their 'curious listening' response. I'll let you know as soon as they do!")
        return WEEK1_FEEDBACK
    else:
        await chat.send_msg("Please send a voice note 'curious listening response' to your partner's stories.")

async def handle_feedback(update, context):
    global START_DATE
    chat = await initialize_chat_handler(update, context)
    if update.message.voice:
        chat.status = f'received_week{chat.week}_feedback'
        await get_voicenote(update, context)
        await chat.send_msg(f"Thanks for sharing that final reflection, {chat.first_name}! You will both receive your final reflections when you have both completed this step.")
        paired_chat = chat_handlers[chat.paired_chat_id]
        if paired_chat.status == f'received_week{chat.week}_feedback':
            send_time = START_DATE + (INTERVAL * 6)
            for c, oc in [(chat, paired_chat),(paired_chat, chat)]:
                c.status = 'week1_complete'
                messages = [
                    "Wonderful, your partner has submitted their final response as well, which means that you can listen to it right now!",
                    f"audio:{await oc.get_audio('received_week1_feedback')}",
                    f"This marks the end of Week {chat.week} of your Echo journey. We hope it has been valuable and reflective, so far. As you know, Echo consists of two weeks, which will start coming Monday. You will get the opportunity to share another story with your partner and try out some more curious listening. Enjoy the rest of your day, and keep an eye out for further steps."
                ]
                await c.send_msgs(messages, send_time)
            if chat.week == 2:
                return END
            if START_DATE == ORIGINAL_START_DATE: # Bit hacky
                START_DATE = START_DATE + INTERVAL * 7
            send_time = START_DATE
            for c, oc in [(chat, paired_chat),(paired_chat, chat)]:
                c.week = 2
                messages = [
                    "Welcome to week two of the Echo experience!",
                    "Your personal story prompt for this week is 'What was an experience in your life where you had to handle a complex situation?'",
                    "Take your time to think about this prompt, and submit your audio when you are ready. Don’t worry too much about what your Echo partner might think—Echo is also about being compassionate, to yourself and others. Rest assured that you will be met with compassion.",
                    "Make sure you send in your story today, your partner will be doing the same."
                ]
                c.status = f'awaiting_week{chat.week}_prompt'
                await c.send_msgs(messages, send_time)
            return WEEK2_PROMPT
        else:
            await chat.send_msg(f"Your partner has not yet sent their feedback. You'll receive it as soon as they do!")
    else:
        await chat.send_msg("Please send a voice note with your feedback on this week's Echo experience.")


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
    #voice_handler = MessageHandler(filters.VOICE, get_voicenote)
    tutorial_handler = ConversationHandler(
        entry_points=[CommandHandler('start', start)],
        states={
            START_WELCOMED: [MessageHandler(filters.TEXT, start_tutorial)],
            TUTORIAL_STARTED: [MessageHandler(filters.TEXT, get_tutorial_story)],
            TUT_STORY1: [MessageHandler(filters.TEXT | filters.VOICE, tut_story1)],
            TUT_STORY2: [MessageHandler(filters.TEXT | filters.VOICE, tut_story2)],
            TUT_COMPLETED: [MessageHandler(filters.TEXT, tut_completed)],
            AWAITING_INTRO: [MessageHandler(filters.TEXT | filters.VOICE, awaiting_intro)],
            WEEK1_PROMPT: [MessageHandler(filters.TEXT | filters.VOICE, handle_prompt)],
            WEEK1_VT: [MessageHandler(filters.TEXT | filters.VOICE, handle_vt)],
            WEEK1_PS: [MessageHandler(filters.TEXT | filters.VOICE, handle_ps)],
            WEEK1_FEEDBACK: [MessageHandler(filters.TEXT | filters.VOICE, handle_feedback)],
            WEEK2_PROMPT: [MessageHandler(filters.TEXT | filters.VOICE, handle_prompt)],
            WEEK2_VT: [MessageHandler(filters.TEXT | filters.VOICE, handle_vt)],
            WEEK2_PS: [MessageHandler(filters.TEXT | filters.VOICE, handle_ps)],
            WEEK2_FEEDBACK: [MessageHandler(filters.TEXT | filters.VOICE, handle_feedback)]
        },
        fallbacks=[CommandHandler('cancel', cancel)]
    )

    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(tutorial_handler)
    #application.add_handler(week2_handler)
    #application.add_handler(voice_handler)
    application.run_polling()
