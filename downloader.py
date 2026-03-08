import os
import sys
import json
import asyncio
from dotenv import load_dotenv
from telethon import TelegramClient

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

    # Iterate through messages from oldest to newest
    # min_id makes it fetch messages strictly greater than min_id.
    async for message in client.iter_messages(target_group, reverse=True, min_id=min_id):
        # Check if message contains a video
        if message.video:
            if message.id in downloaded_ids:
                print(f"Skipping message {message.id} (already downloaded).")
                continue

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
            except Exception as e:
                sys.stdout.write("\n")
                print(f"Failed to download message {message.id}: {e}")

    print("\nDownload complete!")

if __name__ == '__main__':
    # Run the async main function
    asyncio.run(main())
