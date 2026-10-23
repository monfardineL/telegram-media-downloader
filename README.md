# Telegram Media Downloader

A resilient, feature-rich Python tool to automate downloading media (videos, photos, documents, audio) from Telegram channels, supergroups, and forum topics one by one.

## Features

- **Sequential One-by-One Downloads**: Downloads files strictly one at a time to prevent bandwidth saturation and minimize Telegram rate limits.
- **Telegram Limits & Rate Protection**: Automatic detection and handling of Telegram `FloodWaitError`, automatic re-fetching on expired file references, and exponential backoff retry logic.
- **Download Integrity Guarantee**: Files are streamed into `.part` files, verified against Telegram's exact byte size metadata, and atomically committed to disk upon success. No partial or corrupted files.
- **Sequential Numbering**: Prefixes files with zero-padded sequence numbers (e.g., `0001_filename.mp4`, `0002_filename.mp4`).
- **Resilient Unicode & Encoding Sanitization**:
  - Full Unicode NFC normalization (accents like `ã`, `é`, `ç` are properly precomposed).
  - Removal of illegal filesystem characters (`/`, `\`, `:`, `*`, `?`, `"`, `<`, `>`, `|`, `\0`).
  - Stripping of zero-width and bidirectional override formatting characters.
  - Byte-safe filename truncation (staying safely within Linux ext4 255-byte limits).
- **Forum Topics Support**: Automatically handles Telegram forum topics (`/c/<chat_id>/<topic_id>/<msg_id>`).
- **Resumable State Manifest**: Uses a JSON manifest in the destination folder to remember sequence numbers and downloaded files. Interrupted downloads seamlessly pick up where they left off.
- **Dedicated Authentication Tool**: Includes `auth.py` to check authentication status or log in via terminal.

---

## Installation

1. Ensure Python 3.8+ is installed.
2. Activate your virtual environment:
   ```bash
   source venv/bin/activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

---

## Configuration

Settings can be managed in `.env` or passed via CLI arguments:

```env
# Telegram API Credentials (from https://my.telegram.org/apps)
API_ID=your_api_id
API_HASH=your_api_hash
PHONE_NUMBER=+1234567890

# Target Channel and Topic
CHANNEL_URL=https://t.me/c/4345651366/2
GROUP_ID=-1004345651366
TOPIC_ID=2

# First message to download (inclusive)
STARTING_MESSAGE_ID=16

# Output folder
DESTINATION_FOLDER=./downloads

# Sequence numbering padding width (default: 4 -> 0001)
SEQUENCE_PADDING=4

# Media filter: all, or comma-separated: video, photo, document, audio
MEDIA_TYPES=all

# Delay in seconds between downloads
DOWNLOAD_DELAY=1.5

# Max retries per file
MAX_RETRIES=5
```

---

## Authentication

Before running the downloader, make sure you are authenticated with Telegram.

To check your login status:
```bash
./venv/bin/python auth.py --check
```

To log in:
```bash
./venv/bin/python auth.py
```
You will be prompted for your phone number, the verification code sent to your Telegram app, and your 2FA password (if enabled).

---

## Usage

### 1. Run with `.env` settings
```bash
./venv/bin/python downloader.py
```

### 2. Override options on the command line
```bash
# Download starting at message 16 from a topic:
./venv/bin/python downloader.py --channel https://t.me/c/4345651366/2 --start-id 16

# Specify custom destination:
./venv/bin/python downloader.py --dest /path/to/my_downloads
```

### 3. Retry Failed Downloads
Any messages that fail all retry attempts are recorded in `failed-media.list`. You can re-attempt downloading them anytime:
```bash
./venv/bin/python downloader.py --retry-failed
```
