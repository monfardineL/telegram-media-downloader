import os
import sys
import json
import re
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import FloodWaitError

# Load environment variables
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
GROUP_ID = os.getenv("GROUP_ID")
STARTING_MESSAGE_ID = os.getenv("STARTING_MESSAGE_ID")
DESTINATION_FOLDER = os.getenv("DESTINATION_FOLDER", "./downloads")

TRACKING_FILE = "downloaded_ids.json"

if not API_ID or not API_HASH or API_ID == "your_api_id" or API_HASH == "your_api_hash":
    print("Error: API_ID and API_HASH must be set in the .env file.")
    sys.exit(1)

# Ensure destination folder exists
os.makedirs(DESTINATION_FOLDER, exist_ok=True)

# Load downloaded IDs
downloaded_ids = []
if os.path.exists(TRACKING_FILE):
    with open(TRACKING_FILE, "r") as f:
        try:
            downloaded_ids = json.load(f)
        except json.JSONDecodeError:
            downloaded_ids = []

def save_downloaded_id(message_id):
    if message_id not in downloaded_ids:
        downloaded_ids.append(message_id)
        with open(TRACKING_FILE, "w") as f:
            json.dump(downloaded_ids, f)

async def process_message(message, client):
    if not message.video:
        return True

    if message.id in downloaded_ids:
        print(f"Skipping message {message.id} (already downloaded).")
        return True

    # Determine filename
    original_filename = "video.mp4"
    if message.file and message.file.name:
        original_filename = os.path.basename(message.file.name)

    base_name, ext = os.path.splitext(original_filename)
    if not ext:
        ext = ".mp4"

    # Check for name collision
    file_path = os.path.join(DESTINATION_FOLDER, original_filename)
    if os.path.exists(file_path):
        # Append message ID to avoid collision
        new_filename = f"{base_name}_{message.id}{ext}"
        file_path = os.path.join(DESTINATION_FOLDER, new_filename)
    else:
        new_filename = original_filename

    print(f"Downloading video from message {message.id} -> {new_filename}...")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            # Provide a simple progress callback
            def callback(current, total):
                if total:
                    percentage = (current / total) * 100
                    # Print on the same line
                    sys.stdout.write(f"\rDownloading... {percentage:.1f}% ({current}/{total} bytes)")
                else:
                    sys.stdout.write(f"\rDownloading... {current} bytes")
                sys.stdout.flush()

            await message.download_media(file=file_path, progress_callback=callback)
            sys.stdout.write("\n") # Newline after progress

            save_downloaded_id(message.id)
            print(f"Successfully downloaded: {new_filename}")

            # Gentle delay to avoid hitting limits
            await asyncio.sleep(1)
            return True

        except FloodWaitError as e:
            sys.stdout.write("\n")
            wait_time = e.seconds
            print(f"FloodWaitError: Need to wait {wait_time} seconds before retrying...")
            await asyncio.sleep(wait_time)

        except Exception as e:
            sys.stdout.write("\n")
            error_str = str(e)
            print(f"Failed to download message {message.id} (Attempt {attempt + 1}/{max_retries}): {e}")

            if "file reference has expired" in error_str.lower() or "GetFileRequest" in error_str:
                print("File reference expired. Refreshing message...")
                try:
                    chat = await message.get_chat()
                    refreshed_messages = await client.get_messages(chat, ids=[message.id])
                    if refreshed_messages and refreshed_messages[0]:
                        message = refreshed_messages[0]
                except Exception as refresh_e:
                    print(f"Failed to refresh message: {refresh_e}")

            # If it's not the last attempt, wait a bit before trying again
            if attempt < max_retries - 1:
                await asyncio.sleep(2)

    return False

async def main():
    # Initialize the client
    # The session file will be named 'downloader.session'
    try:
        api_id_int = int(API_ID)
    except ValueError:
        print("Error: API_ID must be an integer.")
        sys.exit(1)

    client = TelegramClient('downloader', api_id_int, API_HASH)

    if PHONE_NUMBER:
        await client.start(phone=PHONE_NUMBER)
    else:
        await client.start()

    print("Successfully connected to Telegram.")

    # Determine the group to download from
    target_group = None
    if GROUP_ID:
        try:
            target_group = await client.get_entity(int(GROUP_ID) if GROUP_ID.lstrip('-').isdigit() else GROUP_ID)
        except Exception as e:
            print(f"Could not find group with ID {GROUP_ID}: {e}")
            target_group = None

    if not target_group:
        print("\nFetching your recent dialogs...")
        dialogs = await client.get_dialogs(limit=20)

        # Filter for groups and channels
        chat_dialogs = [d for d in dialogs if d.is_group or d.is_channel]

        print("\nRecent Groups/Chats:")
        for i, dialog in enumerate(chat_dialogs):
            print(f"[{i}] {dialog.name} (ID: {dialog.id})")

        while True:
            try:
                choice = input("\nEnter the number of the group you want to download from (or its ID directly): ")
                if choice.isdigit() and int(choice) < len(chat_dialogs):
                    target_group = chat_dialogs[int(choice)]
                    break
                else:
                    # User might have pasted an ID
                    target_group = await client.get_entity(int(choice) if choice.lstrip('-').isdigit() else choice)
                    break
            except Exception as e:
                print(f"Invalid choice or group not found. Try again. ({e})")

    group_name = target_group.title if hasattr(target_group, 'title') else target_group.id
    print(f"\nTarget group selected: {group_name}")

    # Set up starting message ID
    min_id = 0
    if STARTING_MESSAGE_ID and STARTING_MESSAGE_ID.isdigit():
        min_id = int(STARTING_MESSAGE_ID)
        print(f"Starting after message ID: {min_id}")

    print("Fetching messages...")

    failed_media_file = None
    if len(sys.argv) > 1:
        failed_media_file = sys.argv[1]

    if failed_media_file and os.path.exists(failed_media_file):
        print(f"Reading message IDs from {failed_media_file}...")
        message_ids = []
        with open(failed_media_file, "r") as f:
            for line in f:
                # Try to extract the first sequence of digits which is likely the message ID
                match = re.search(r'\b(\d+)\b', line)
                if match:
                    message_ids.append(int(match.group(1)))

        # Remove duplicates
        message_ids = list(dict.fromkeys(message_ids))
        print(f"Found {len(message_ids)} message IDs to retry.")

        for msg_id in message_ids:
            try:
                # Fetch the specific message
                message = await client.get_messages(target_group, ids=msg_id)
                if message:
                    success = await process_message(message, client)
                    if not success:
                        with open("failed-media.list", "a") as f_out:
                            f_out.write(f"{msg_id}\n")
                else:
                    print(f"Message {msg_id} not found.")
                    with open("failed-media.list", "a") as f_out:
                        f_out.write(f"{msg_id}\n")
            except Exception as e:
                print(f"Failed to process message {msg_id}: {e}")
                with open("failed-media.list", "a") as f_out:
                    f_out.write(f"{msg_id}\n")
    else:
        if failed_media_file:
            print(f"Warning: The file {failed_media_file} does not exist. Ignoring.")

        # Iterate through messages from oldest to newest
        # min_id makes it fetch messages strictly greater than min_id.
        async for message in client.iter_messages(target_group, reverse=True, min_id=min_id):
            success = await process_message(message, client)
            if not success:
                with open("failed-media.list", "a") as f_out:
                    f_out.write(f"{message.id}\n")

    print("\nDownload complete!")

if __name__ == '__main__':
    # Run the async main function
    asyncio.run(main())
