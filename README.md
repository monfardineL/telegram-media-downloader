# Telegram Group Media Downloader

This is a simple, resilient Python script that uses the Telegram MTProto API (via the `Telethon` library) to authenticate your user account and automate downloading videos from a specific Telegram group or channel.

It features:
- Interactive group selection
- Video-only filtering
- Resumable downloads (keeps track of already downloaded files)
- Original filename preservation (with fallback and collision handling)

## Prerequisites

- Python 3.7+
- A Telegram account
- Your Telegram API credentials (`api_id` and `api_hash`)

### Getting your Telegram API Credentials

1. Go to [https://my.telegram.org](https://my.telegram.org) and log in with your phone number.
2. Click on **"API development tools"**.
3. Create a new application (you can enter anything for the App title and Short name).
4. You will get an **`api_id`** (a number) and an **`api_hash`** (a string). Keep these safe!

## Installation

1. Clone or download this repository.
2. (Optional but recommended) Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```
3. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Configuration

1. Copy the `.env.example` file to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Open the `.env` file and fill in your details:
   - `API_ID`: Your API ID from my.telegram.org
   - `API_HASH`: Your API Hash from my.telegram.org
   - `PHONE_NUMBER`: Your phone number (with country code, e.g., `+1234567890`). This helps the script log in automatically.
   - `GROUP_ID` (Optional): If you know the exact ID of the group/channel, put it here. If left blank, the script will show you a list of your recent chats to pick from.
   - `STARTING_MESSAGE_ID` (Optional): The ID of the message to start downloading *after*. If you want to download everything, leave this blank.
   - `DESTINATION_FOLDER`: The folder where videos will be saved. Defaults to `./downloads`.

## Usage

Run the script:

```bash
python downloader.py
```

### First Run (Authentication)
The first time you run the script, Telegram will send a login code to your Telegram app (or via SMS). The script will prompt you to enter this code in the terminal. Once entered, a file named `downloader.session` will be created locally. Subsequent runs will use this session file and won't require you to log in again.

### Group Selection
If you didn't provide a `GROUP_ID` in your `.env` file, the script will list your recent 20 groups/channels. You can simply type the number corresponding to the group you want to download from.

### Resuming
The script creates a `downloaded_ids.json` file to keep track of every message ID it has successfully downloaded. If you stop the script halfway through and run it again later, it will automatically skip any videos it has already downloaded.
