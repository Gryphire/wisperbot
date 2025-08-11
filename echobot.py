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
import asyncio
from telegram.error import TimedOut

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

def get_pair_start_date(chat, paired_chat):
    """Get the appropriate start date for a user pair (later of the two start dates)"""
    return max(chat.start_date, paired_chat.start_date)

async def initialize_chat_handler(update,context=None):
    chat_id = update.effective_chat.id
    if chat_id not in chat_handlers:
        chat_handlers[chat_id] = ChatHandler(chat_id, update, context, START_DATE)
        chat = chat_handlers[chat_id]
    chat = chat_handlers[chat_id]
    chat.update = update
    return chat

async def get_voicenote(update: Update, context: CallbackContext) -> None:
    chat = await initialize_chat_handler(update,context)
    path = os.path.join(chat.directory, chat.subdir)
    os.makedirs(path, exist_ok=True)
    
    # Retry logic for downloading the file
    retries = 3
    for attempt in range(retries):
        try:
            # get basic info about the voice note file and prepare it for downloading
            new_file = await context.bot.get_file(update.message.voice.file_id)
            break
        except TimedOut:
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
            else:
                raise

    # We used to get the now timestamp:
    # ts = datetime.now().strftime("%Y%m%d-%H:%M")
    # But probably better to get the timestamp of the message:
    ts = update.message.date.strftime("%Y%m%d-%H:%M")
    # download the voice note as a file
    filename = f"{ts}-{update.message.from_user.first_name}-{chat.chat_id}-{chat.status}-{new_file.file_unique_id}.ogg"
    filepath = os.path.join(path, filename)
    await new_file.download_to_drive(filepath)
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
        await chat.send_msg("""Alright, next up you'll have the opportunity to listen to two personal stories. When you're done, think about what values are at play in their story. Take a minute or two to record a response to this person's story in an 'active listening' way.\n\nActive listening means reflecting back what the person is saying, and asking them clarifying questions. In this tutorial, your response will NOT be sent back to the story's author, so don't be afraid to practice! ^^\nYou'll get your second and final tutorial story after you complete this one.\n\nReady to listen to some stories? Please run /gettutorialstory to receive a tutorial story to start with.""")
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
        await chat.send_msg(f"""So, having listened to this person's story, what do you think is the rub? Which driving forces underlie the storyteller's experience?\n\nWhen you're ready to send in an audio response to this story, just record and send it to Echobot.\n\nRemember to reflect on the which values seems to drive the person in this story but do so through 'active listening': by paraphrasing and asking clarifying questions.\n\nRecord your response whenever you're ready!""")
        chat.status = f'tut_story{chat.week}received'
        chat.log_recv_text('Received /gettutorialstory')
        return TUT_STORY1
    else:
        chat.log_recv_text(text=update.message.text)
        await chat.send_msg("""Please run /gettutorialstory""")

async def handle_voice_or_text(update, context, chat, cur_state, done_message, next_state=None):
    if not hasattr(chat, 'voice_count'):
        chat.voice_count = 0

    if update.message.text and update.message.text != '/done':
        await chat.send_msg("Please send a voicenote response 😊")
        return cur_state
    elif update.message.voice:
        chat.voice_count += 1
        await get_voicenote(update, context)
        if chat.voice_count == 1:
            await chat.send_msg(f"Thank you for recording your response, {chat.first_name}! You can send one more if you need to, or type /done if you're finished.")
            return cur_state
        elif chat.voice_count == 2:
            await chat.send_msg(done_message)
            chat.voice_count = 0
            return next_state if next_state else cur_state
    elif update.message.text == '/done':
        if chat.voice_count > 0:
            await chat.send_msg(done_message)
            chat.voice_count = 0
            return next_state if next_state else cur_state
        else:
            await chat.send_msg("Please send at least one voicenote before using /done.")
            return cur_state
    return cur_state

async def tut_story1(update, context):
    chat = await initialize_chat_handler(update, context)
    done_message = f"""Here's the second tutorial story for you to listen to, from someone else:"""
    chat.status = f'tut_story{chat.week}responded'
    next_state = await handle_voice_or_text(update, context, chat, TUT_STORY1, done_message, TUT_STORY2)
    
    if next_state == TUT_STORY2:
        await chat.send_vn(VN=f'tutorialstories/{tutorial_files[1]}')
        await chat.send_msg("""Again, have a think about which values seem embedded in this person's story. When you're ready to record your response, go ahead!""")
    
    return next_state

async def tut_story2(update, context):
    chat = await initialize_chat_handler(update, context)
    done_message = """Exciting! You've listened to all the tutorial stories we've got for you!

Time to enter the main EchoBot experience, where you will also be able to listen to another person's stories, and send them a 'curious listening' response about the values they seem to balance, just like you have done in the tutorial.

Additionally, and importantly, your will also get to record your own stories, based on a prompt! Other people will then be able respond to your story, the same way you have responded to theirs.

Use the /endtutorial command to enter the world of EchoBot!"""
    chat.status = f'tut_story2responded'
    return await handle_voice_or_text(update, context, chat, TUT_STORY2, done_message, TUT_COMPLETED)

async def tut_completed(update, context):
    chat = await initialize_chat_handler(update, context)
    if update.message.text == '/endtutorial':
        chat.status = 'tut_completed'
        await chat.send_msg(f"Now that the tutorial part of EchoBot has been completed, you will be participating in Echo together with a randomly assigned peer! We think it's nice for both you and your partner to introduce yourselves shortly to one another.\n\nFeel free to include your first name if you want to, but you are not required to do so.\n\nPlease send a voicenote introducing yourself to your partner today. Once both you and your partner have done so, you will be able to continue on to the main Echo experience tomorrow.")
        chat.subdir = 'intros'
        chat.status = 'awaiting_intro'
        return AWAITING_INTRO
    else:
        await chat.send_msg("Please run /endtutorial")

async def awaiting_intro(update, context):
    '''Await introductions. Once both are received, exchange them between users, then schedule the first prompt.'''
    chat = await initialize_chat_handler(update, context)
    done_message = "Thank you for recording your introduction!"
    next_state = await handle_voice_or_text(update, context, chat, AWAITING_INTRO, done_message, WEEK1_PROMPT)

    if next_state == WEEK1_PROMPT:
        chat.status = 'received_intro'
        if hasattr(chat, 'paired_chat_id') and chat.paired_chat_id in chat_handlers:
            paired_chat = chat_handlers[chat.paired_chat_id]
            if paired_chat.status == 'received_intro':
                await chat.exchange_vns(paired_chat, status='awaiting_intro', Text="Your partner has also sent in their introduction!")
                # Use the later start date to ensure both users are ready
                pair_start_date = get_pair_start_date(chat, paired_chat)
                send_time = pair_start_date + INTERVAL
                for c in (chat, paired_chat):
                    c.status = 'intros_complete'
                    await c.send_msg("Day 1 complete!")
                    messages = [
                        "Welcome to the main Echo experience! You have successfully completed on-boarding, and have been already introduced to your Echo partner.",
                        "Over the course of this week, you will be exchanging audio messages and listening to one another. Today, we start with reflecting on your life and record an audio story for your partner. You will do so based on a prompt that you and your partner both will receive in a moment.",
                        "Your personal story prompt is 'What was an experience in your life where you had to handle a complex situation?'",
                        "Take your time to think about this prompt, and submit your voice message (anywhere between 2 and 5 minutes is fine!) when you are ready. Don't worry too much about what your Echo partner might think—Echo is also about being compassionate, to yourself and others. Rest assured that you will be met with compassion.",
                        "Make sure you send in your story today, your partner will be doing the same. (;"
                    ]
                    await c.send_msgs(messages, send_time)
                    c.status = f'awaiting_week{chat.week}_prompt'
            else:
                await chat.send_msg("Your partner has not yet sent their introduction. We'll keep you posted!")
        else:
            await chat.send_msg("Your partner has not yet sent their introduction. You'll receive it as soon as they send it in!")
    return next_state

async def handle_prompt(update, context):
    chat = await initialize_chat_handler(update, context)
    chat.subdir = f'week{chat.week}'
    if context.job_queue.jobs():
        await chat.send_msg("Please wait until we send you the next prompt :)")
        return eval(f"WEEK{chat.week}_PROMPT")
    done_message = f"""Thank you for submitting your audio story, {chat.first_name}! Your story has been saved, but will not yet be sent to your partner. Before that happens, you will be asked to complete one more part tomorrow.

Stay tuned, you will continue the Echo journey tomorrow morning and receive further instructions then!"""
    chat.status = f'received_week{chat.week}_story'
    next_state = await handle_voice_or_text(update, context, chat, eval(f"WEEK{chat.week}_PROMPT"), done_message, eval(f"WEEK{chat.week}_VT"))
    
    if next_state == eval(f"WEEK{chat.week}_VT"):
        paired_chat = chat_handlers[chat.paired_chat_id]
        if paired_chat.status == f'received_week{chat.week}_story':
            # Handle the case when both partners have submitted their stories
            pair_start_date = get_pair_start_date(chat, paired_chat)
            send_time = pair_start_date + (INTERVAL * 2)
            for c in (chat, paired_chat):
                c.status = f'week{chat.week}_day2_complete'
                messages = [
                    "Welcome back! Today, we would like you to listen to the story that you recorded yesterday, and think about how you have had to balance between different values in that situation. This is what we call 'value tensions'.",
                    "Here are some examples of value tensions.",
                    'img:vt.png',
                    "In today's part of the Echo experience, we would like you to reflect on how you would place yourself on one or two of these value tensions, and send this to your Echo partner in a voice message. You do not need to cover all of these tensions! Just pick one or two that seem relevant to the story that you recorded earlier. When you are ready to record, go ahead." 
                ]
                await c.send_msgs(messages, send_time)
                c.status = f'awaiting_week{chat.week}_vt'
            return eval(f"WEEK{chat.week}_VT")  # Return the next state explicitly
        else:
            await chat.send_msg(f"Your partner has not yet sent their story. I'll let you know as soon as they do!")
            return eval(f"WEEK{chat.week}_VT")
    
    return next_state

async def handle_vt(update, context):
    chat = await initialize_chat_handler(update, context)
    done_message = f"""Thank you for sending in your value tension reflection, {chat.first_name}!

Now that you have recorded a personal story ánd a value tension reflection on that story, you and your Echo partner will both receive each other's stories tomorrow morning.

Stay tuned, you will continue the Echo journey in the coming days."""
    chat.status = f'received_week{chat.week}_vt'
    next_state = await handle_voice_or_text(update, context, chat, eval(f"WEEK{chat.week}_VT"), done_message, eval(f"WEEK{chat.week}_PS"))
    
    if next_state == eval(f"WEEK{chat.week}_PS"):
        paired_chat = chat_handlers[chat.paired_chat_id]
        if paired_chat.status == f'received_week{chat.week}_vt':
            pair_start_date = get_pair_start_date(chat, paired_chat)
            send_time = pair_start_date + (INTERVAL * 3)
            for c, oc in [(chat, paired_chat), (paired_chat, chat)]:
                c.status = f'week{chat.week}_day3_complete'
                await c.send_msg(f"Hi there. Just a heads up that your partner has now also sent in their reflection. Stay tuned for the next steps tomorrow!")
                messages = [
                    "Hi there! Your partner and you both have recorded your story and value tension reflections. Time to listen to your partner's audio!",
                    "Here is your partner's initial personal story."
                ]
                # Get and send initial personal story audios
                initial_story_audios = await oc.get_audio(f'received_week{chat.week}_story')
                for audio in initial_story_audios:
                    messages.append(f"audio:{audio}")
                
                messages.append("Here is your partner's value tension reflection on that story.")
                
                # Get and send value tension reflection audios
                vt_reflection_audios = await oc.get_audio(f'received_week{chat.week}_vt')
                for audio in vt_reflection_audios:
                    messages.append(f"audio:{audio}")
                
                messages.extend([
                    "Now, it's important that you listen to these stories as you would to a good friend.  It's important to be what we call a 'curious listener', a person who listens to understand rather than respond. Try to focus on understanding the other person, their perspective, and feelings. When you respond try to reflect their message back to them in your own words and ask a few clarifying or elaborating questions. This helps the other person to feel heard and understood.",
                    "Go ahead and record your response whenever you're ready, but make sure to do this by tomorrow night."
                ])
                
                await c.send_msgs(messages, send_time)
                c.status = 'awaiting_listening_response'
        else:
            await chat.send_msg(f"Your partner has not yet completed their reflection. I'll let you know as soon as they do!")
    
    return next_state

async def handle_ps(update, context):
    '''Handle the response for week 1 personal story reflection'''
    chat = await initialize_chat_handler(update, context)
    done_message = f"""Thank you for recording your response! I'm sure that your partner {chat_handlers[chat.paired_chat_id].first_name if chat_handlers[chat.paired_chat_id].first_name else ''} will be grateful to hear your attentive perspective!

Stay tuned and keep an eye out for next steps from me in the coming days!"""
    chat.status = f'received_week{chat.week}_ps'
    next_state = await handle_voice_or_text(update, context, chat, eval(f"WEEK{chat.week}_PS"), done_message, eval(f"WEEK{chat.week}_FEEDBACK"))
    
    if next_state == eval(f"WEEK{chat.week}_FEEDBACK"):
        paired_chat = chat_handlers[chat.paired_chat_id]
        if paired_chat.status == f'received_week{chat.week}_ps':
            pair_start_date = get_pair_start_date(chat, paired_chat)
            send_time = pair_start_date + (INTERVAL * 5)
            for c, oc in [(chat, paired_chat), (paired_chat, chat)]:
                c.status = f'week{chat.week}_day4_complete'
                await c.send_msg(f"Hi! Just a heads up; your partner has also sent in their 'curious listening' response.")
                messages = [
                    "Hey! After sending your active listening response to your partner yesterday, today its time to hear your partners perspective. Take a listen!"
                ]
                
                # Get and send personal story reflection audios
                ps_reflection_audios = await oc.get_audio(f'received_week{chat.week}_ps')
                for audio in ps_reflection_audios:
                    messages.append(f"audio:{audio}")
                
                messages.extend([
                    "Take some time to reflect on their message. How has it shifted your perspective and thoughts? Did they think of a connection or idea you didn't?",
                    "Record a final message in response by midnight today. Feel free to thank them for their thoughts and what they have shared. Exchange your thoughts and feelings. Tomorrow you will get a chance to hear their final response to your insights as well."
                ])
                
                await c.send_msgs(messages, send_time)
                c.status = f'awaiting_week{chat.week}_feedback'
        else:
            await chat.send_msg(f"Your partner has not yet sent their 'curious listening' response. I'll let you know as soon as they do!")
    
    return next_state

async def handle_feedback(update, context):
    chat = await initialize_chat_handler(update, context)
    paired_chat = chat_handlers[chat.paired_chat_id]

    done_message = f"""Thanks for sharing that final reflection, {chat.first_name}! You will both receive your final reflections when you have both completed this step."""
    
    current_state = eval(f"WEEK{chat.week}_FEEDBACK")  # Correct current state
    next_state = await handle_voice_or_text(update, context, chat, current_state, done_message, eval(f"WEEK2_PROMPT"))
    
    if next_state == current_state:
        return next_state  # User hasn't finished their submission yet
    
    # User has finished their submission
    chat.status = f'received_week{chat.week}_feedback'  # Correct status
    
    # Check if both users have submitted their feedback
    if paired_chat.status == f'received_week{chat.week}_feedback':
        # Both users have submitted feedback, proceed to next week or end
        
        # Calculate timing based on each user pair's individual start date
        # Use the later of the two users' start dates to ensure both are ready
        pair_start_date = get_pair_start_date(chat, paired_chat)
        send_time = pair_start_date + (INTERVAL * 6)
        
        for c, oc in [(chat, paired_chat), (paired_chat, chat)]:
            c.status = f'week{chat.week}_complete'
            messages = [
                "Wonderful, your partner has submitted their final response as well, which means that you can listen to it right now!"
            ]
            
            # Get and send final feedback audios
            feedback_audios = await oc.get_audio(f'awaiting_week{chat.week}_feedback')
            for audio in feedback_audios:
                messages.append(f"audio:{audio}")
            
            if chat.week == 2:
                messages.append("This marks the end of your Echo journey. We hope it has been valuable and reflective. Thank you for participating!")
            else:
                messages.append(f"This marks the end of Week {chat.week} of your Echo journey. We hope it has been valuable and reflective, so far. As you know, Echo consists of two weeks, which will start coming Monday. You will get the opportunity to share another story with your partner and try out some more curious listening. Enjoy the rest of your day, and keep an eye out for further steps.")
            
            await c.send_msgs(messages, send_time)
        
        if chat.week == 2:
            return END
        
        # Calculate week 2 start time based on each pair's timeline (no global modification)
        week2_start_time = pair_start_date + (INTERVAL * 7)
        
        for c in [chat, paired_chat]:
            c.week += 1
            # Store week 2 start time for this specific user pair
            if not hasattr(c, 'week2_start_date'):
                c.week2_start_date = week2_start_time
                
            messages = [
                f"Welcome to week {c.week} of the Echo experience!",
                "Your personal story prompt for this week is 'What was a turning point in your life so far?'",
                "Take your time to think about this prompt, and submit your audio when you are ready. Again, don't worry too much about what your Echo partner might think—Echo is also about being compassionate, to yourself and others. Rest assured that you will be met with compassion.",
                "Make sure you send in your story today, your partner will be doing the same."
            ]
            c.status = f'awaiting_week{c.week}_prompt'
            await c.send_msgs(messages, week2_start_time)
        
        return next_state
    else:
        # Partner hasn't submitted feedback yet
        await chat.send_msg(f"Your partner has not yet sent their feedback. You'll receive further instructions as soon as they do!")
        return next_state

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
