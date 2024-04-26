# WisperBot

Telegram bot for Wisper project GEMH Lab.

## Files included

- `echobot.py`: Main bot conversation sequence
- `chat.py`: Includes ChatHandler class which handles communicating with the user and logging

## Configuration

Create a `.env` file.
Options allow you to disable transcription, disable sending of videos, set the start date for an experiment, set how long a "day" lasts, and optionally set the status the bot starts from (for testing purposes).

Sample:

```bash
TELEGRAM_TOKEN = 'your_telegram_token'
OPENAI_API_KEY = 'your_openai_key'
TRANSCRIBE = 'False'
VIDEO = 'False'
START_DATE = datetime(2024,4,16,22,22)
INTERVAL = 'timedelta(seconds=120)'
STARTING_STATUS = 'WEEK1_PROMPT1'
```
