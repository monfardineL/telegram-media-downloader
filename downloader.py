#!/usr/bin/env python3
"""
Telegram Media Downloader
Downloads media from Telegram channels, supergroups, and forum topics one by one.
Ensures file integrity, handles Telegram rate limits, sanitizes filename encoding,
and prefixes sequential numbering (e.g. 0001_filename.mp4).
"""

import os
import sys
import json
import re
import time
import asyncio
import unicodedata
import argparse
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

from telethon import TelegramClient, utils
from telethon.tl import types
from telethon.tl.types import (
    MessageMediaWebPage,
    MessageMediaUnsupported,
    MessageMediaDocument,
    MessageMediaPhoto
)
from telethon.errors import (
    FloodWaitError,
    FileReferenceExpiredError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError
)

# Load environment configuration
load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")

# Target configuration from .env
CHANNEL_URL = os.getenv("CHANNEL_URL")
GROUP_ID = os.getenv("GROUP_ID")
TOPIC_ID = os.getenv("TOPIC_ID")
STARTING_MESSAGE_ID = os.getenv("STARTING_MESSAGE_ID")
DESTINATION_FOLDER = os.getenv("DESTINATION_FOLDER", "./downloads")

# Behavior configuration
SEQUENCE_PADDING = int(os.getenv("SEQUENCE_PADDING", "4"))
MEDIA_TYPES = os.getenv("MEDIA_TYPES", "all").strip().lower()
DOWNLOAD_DELAY = float(os.getenv("DOWNLOAD_DELAY", "1.5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))

TRACKING_FILE = "downloaded_ids.json"
FAILED_LOG_FILE = "failed-media.list"
SESSION_NAME = "downloader"


def format_size(size_bytes: float) -> str:
    """Format bytes to human readable string."""
    if size_bytes is None:
        return "Unknown size"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def parse_telegram_target(
    channel_url: Optional[str] = None,
    group_id: Optional[str] = None,
    topic_id: Optional[Any] = None,
    start_msg_id: Optional[Any] = None,
    all_topics: bool = False
) -> Dict[str, Any]:
    """
    Parses channel URL and IDs to determine chat_id, topic_id, and starting_message_id.
    Handles URLs like:
      - https://t.me/c/4345651366/2/16 (chat, topic, message)
      - https://t.me/c/4345651366/2    (chat, topic)
      - https://t.me/c/4345651366      (chat)
      - Raw IDs like -1004345651366 or 4345651366
    """
    target = {
        "chat_id": None,
        "topic_id": None,
        "starting_message_id": None
    }

    url_candidate = channel_url or (group_id if group_id and "t.me" in str(group_id) else None)
    if url_candidate:
        url_str = str(url_candidate).strip()
        m_c = re.search(r"t\.me/c/(\d+)(?:/(\d+))?(?:/(\d+))?", url_str)
        if m_c:
            c_num, p2, p3 = m_c.groups()
            target["chat_id"] = int(f"-100{c_num}")
            if p3 is not None:
                target["topic_id"] = int(p2)
                target["starting_message_id"] = int(p3)
            elif p2 is not None:
                target["topic_id"] = int(p2)
        else:
            m_user = re.search(r"t\.me/([a-zA-Z0-9_]+)(?:/(\d+))?", url_str)
            if m_user:
                username, msg_id = m_user.groups()
                target["chat_id"] = username
                if msg_id:
                    target["starting_message_id"] = int(msg_id)

    # Fallback to group_id if chat_id not set from URL
    if target["chat_id"] is None and group_id:
        gid_str = str(group_id).strip()
        if gid_str.lstrip("-").isdigit():
            val = int(gid_str)
            # If positive supergroup ID missing -100 prefix, add it
            if val > 0 and len(gid_str) >= 9:
                target["chat_id"] = int(f"-100{val}")
            else:
                target["chat_id"] = val
        else:
            target["chat_id"] = gid_str

    # Topic ID override or all_topics reset
    if all_topics or (topic_id is not None and str(topic_id).strip().lower() in ("all", "none", "*")):
        target["topic_id"] = None
    elif topic_id is not None and str(topic_id).strip() != "":
        try:
            target["topic_id"] = int(topic_id)
        except ValueError:
            pass

    if start_msg_id is not None and str(start_msg_id).strip() != "":
        try:
            target["starting_message_id"] = int(start_msg_id)
        except ValueError:
            pass

    return target


def sanitize_filename(base_name: str, ext: str, seq: Optional[int] = None, padding: int = 4, max_bytes: int = 240) -> str:
    """
    Sanitize and normalize filename to prevent encoding and filesystem errors:
      1. Unicode NFC normalization.
      2. Removal of lone surrogates.
      3. Replacement of forbidden characters (/:*?"<>|\0\n\r\t).
      4. Stripping zero-width, non-printable, and bidirectional control characters.
      5. Safe byte-length truncation to comply with ext4/Linux 255-byte limit.
      6. Prepending zero-padded sequence number (e.g. 0001_).
    """
    # Unicode NFC normalization
    base_name = unicodedata.normalize("NFC", base_name)
    ext = unicodedata.normalize("NFC", ext)

    # Strip surrogate characters
    base_name = "".join(c for c in base_name if not (0xD800 <= ord(c) <= 0xDFFF))
    ext = "".join(c for c in ext if not (0xD800 <= ord(c) <= 0xDFFF))

    # Replace forbidden/control characters
    base_name = re.sub(r"[\x00-\x1f\x7f-\x9f/\\:*?\"<>|\t\n\r]", "_", base_name)
    ext = re.sub(r"[\x00-\x1f\x7f-\x9f/\\:*?\"<>|\t\n\r]", "", ext)

    # Remove zero-width spaces and bidi override characters
    invisible_chars = {
        0x200B, 0x200C, 0x200D, 0xFEFF, 0x00A0,
        0x202A, 0x202B, 0x202C, 0x202D, 0x202E,
        0x2066, 0x2067, 0x2068, 0x2069
    }
    base_name = "".join(c for c in base_name if ord(c) not in invisible_chars)

    # Collapse repeated spaces/underscores and strip edge dots/spaces
    base_name = re.sub(r"[ _]+", "_", base_name).strip(" ._")

    if not base_name:
        base_name = "media"

    if ext and not ext.startswith("."):
        ext = "." + ext

    # Format sequence prefix
    prefix = f"{seq:0{padding}d}_" if seq is not None else ""

    # Truncate base_name by UTF-8 bytes to fit max_bytes
    prefix_bytes = prefix.encode("utf-8")
    ext_bytes = ext.encode("utf-8")
    avail_bytes = max_bytes - len(prefix_bytes) - len(ext_bytes)
    if avail_bytes < 10:
        avail_bytes = 10

    base_bytes = base_name.encode("utf-8")
    if len(base_bytes) > avail_bytes:
        base_name = base_bytes[:avail_bytes].decode("utf-8", errors="ignore").rstrip(" ._")

    return f"{prefix}{base_name}{ext}"


def determine_media_filename(message, seq_num: int, padding: int = 4) -> str:
    """Extract or generate a clean filename with sequence prefix for a Telegram message."""
    original_name = None
    ext = None

    # Try original document filename
    if message.file and message.file.name:
        original_name = os.path.basename(message.file.name)
        base, detected_ext = os.path.splitext(original_name)
        if detected_ext:
            ext = detected_ext
            original_name = base

    # If no filename, check caption or message text
    if not original_name and (message.message or message.text):
        caption = (message.message or message.text).strip().splitlines()[0]
        caption = re.sub(r"https?://\S+", "", caption).strip()
        if caption:
            original_name = caption[:60]

    # Detect extension if missing
    if not ext:
        if message.file and message.file.ext:
            ext = message.file.ext
        elif message.video:
            ext = ".mp4"
        elif message.photo:
            ext = ".jpg"
        elif message.audio:
            ext = ".mp3"
        elif message.voice:
            ext = ".ogg"
        else:
            ext = ".mp4"

    if not original_name:
        if message.video:
            original_name = f"video_{message.id}"
        elif message.photo:
            original_name = f"photo_{message.id}"
        else:
            original_name = f"media_{message.id}"

    return sanitize_filename(original_name, ext, seq=seq_num, padding=padding)


class DownloadManifest:
    """
    Manages persistent state of downloads in the destination folder.
    Ensures sequential numbering continuity, verifies file integrity on disk,
    and maintains backward compatibility with downloaded_ids.json.
    """
    def __init__(self, dest_dir: str, chat_id: Any = None, topic_id: Any = None):
        self.dest_dir = dest_dir
        self.manifest_file = os.path.join(dest_dir, "manifest.json")
        self.data = {
            "chat_id": chat_id,
            "topic_id": topic_id,
            "downloads": {}
        }
        self.load()
        if chat_id:
            self.data["chat_id"] = chat_id
        if topic_id:
            self.data["topic_id"] = topic_id

    def load(self):
        if os.path.exists(self.manifest_file):
            try:
                with open(self.manifest_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict) and "downloads" in loaded:
                        self.data = loaded
            except Exception as e:
                print(f"Notice: Could not load existing manifest: {e}")

    def save(self):
        temp_file = self.manifest_file + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
            os.replace(temp_file, self.manifest_file)
        except Exception as e:
            print(f"Warning: Could not save manifest: {e}")

    def is_downloaded(self, message_id: int) -> bool:
        """Returns True only if marked completed and file exists on disk with non-zero size."""
        entry = self.data["downloads"].get(str(message_id))
        if entry and entry.get("status") == "completed":
            filename = entry.get("filename")
            if filename:
                filepath = os.path.join(self.dest_dir, filename)
                if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                    return True
        return False

    def get_existing_filename(self, message_id: int) -> Optional[str]:
        entry = self.data["downloads"].get(str(message_id))
        return entry.get("filename") if entry else None

    def get_next_sequence(self, padding: int = 4) -> int:
        """Finds the next sequential number based on manifest and files present on disk."""
        seqs = [
            e["sequence"]
            for e in self.data["downloads"].values()
            if isinstance(e, dict) and "sequence" in e and isinstance(e["sequence"], int)
        ]
        # Also scan disk for files matching the sequence prefix
        if os.path.exists(self.dest_dir):
            for fname in os.listdir(self.dest_dir):
                m = re.match(r"^(\d{" + str(padding) + r",})_", fname)
                if m:
                    try:
                        seqs.append(int(m.group(1)))
                    except ValueError:
                        pass
        return max(seqs, default=0) + 1

    def record_success(self, message_id: int, sequence: int, filename: str, size: int):
        self.data["downloads"][str(message_id)] = {
            "sequence": sequence,
            "filename": filename,
            "size": size,
            "status": "completed",
            "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        }
        self.save()

        # Update backward-compatible downloaded_ids.json
        self._sync_downloaded_ids_json(message_id)

    def record_failure(self, message_id: int, error_msg: str):
        if str(message_id) not in self.data["downloads"]:
            self.data["downloads"][str(message_id)] = {}
        self.data["downloads"][str(message_id)].update({
            "status": "failed",
            "last_error": error_msg,
            "failed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        })
        self.save()

    def _sync_downloaded_ids_json(self, message_id: int):
        try:
            ids = []
            if os.path.exists(TRACKING_FILE):
                with open(TRACKING_FILE, "r", encoding="utf-8") as f:
                    ids = json.load(f)
            if message_id not in ids:
                ids.append(message_id)
                with open(TRACKING_FILE, "w", encoding="utf-8") as f:
                    json.dump(ids, f)
        except Exception:
            pass


def is_downloadable_media(message, media_filter: str = "all") -> bool:
    """Checks if message contains downloadable media matching the filter."""
    if not message or not message.media:
        return False
    if isinstance(message.media, (MessageMediaWebPage, MessageMediaUnsupported)):
        return False

    if media_filter == "all":
        return True

    allowed = [m.strip() for m in media_filter.split(",")]
    if "video" in allowed and (message.video or (message.document and getattr(message.file, "mime_type", "").startswith("video/"))):
        return True
    if "photo" in allowed and message.photo:
        return True
    if "audio" in allowed and (message.audio or message.voice or getattr(message.file, "mime_type", "").startswith("audio/")):
        return True
    if "document" in allowed and message.document:
        return True

    return False


async def download_file_with_verification(message, final_path: str, client: TelegramClient) -> bool:
    """
    Downloads media to a temporary .part file, verifies file size,
    and atomically renames to final_path upon completion.
    """
    part_path = f"{final_path}.part"
    expected_size = None
    if message.file and message.file.size:
        expected_size = message.file.size

    start_time = time.time()
    last_print_time = 0

    def progress_callback(current: int, total: Optional[int]):
        nonlocal last_print_time
        now = time.time()
        # Throttle stdout writes to max 10 updates per second
        if (now - last_print_time) < 0.1 and total and current < total:
            return
        last_print_time = now

        elapsed = now - start_time
        speed = current / elapsed if elapsed > 0 else 0
        speed_str = f"{format_size(speed)}/s"

        if total:
            pct = (current / total) * 100
            sys.stdout.write(
                f"\r  [Progress] {pct:5.1f}% ({format_size(current)} / {format_size(total)}) @ {speed_str}    "
            )
        else:
            sys.stdout.write(
                f"\r  [Progress] {format_size(current)} @ {speed_str}    "
            )
        sys.stdout.flush()

    try:
        # Download directly to .part
        await message.download_media(file=part_path, progress_callback=progress_callback)
        sys.stdout.write("\n")
        sys.stdout.flush()

        # Integrity checks
        if not os.path.exists(part_path):
            raise IOError("Downloaded file does not exist on disk.")

        actual_size = os.path.getsize(part_path)
        if actual_size == 0:
            raise IOError("Downloaded file is empty (0 bytes).")

        if expected_size and actual_size != expected_size:
            raise IOError(
                f"File size mismatch: expected {expected_size} bytes ({format_size(expected_size)}), "
                f"got {actual_size} bytes ({format_size(actual_size)})"
            )

        # Atomic replacement to destination filename
        os.replace(part_path, final_path)
        return True

    except Exception as e:
        sys.stdout.write("\n")
        sys.stdout.flush()
        # Clean up partial file on failure
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass
        raise e


async def process_single_message(
    message,
    client: TelegramClient,
    manifest: DownloadManifest,
    dest_folder: str,
    target_entity: Any,
    padding: int = 4
) -> bool:
    """
    Processes and downloads a single message with retries, FloodWait handling,
    file reference refresh, and sequence number assignment.
    """
    if not is_downloadable_media(message, MEDIA_TYPES):
        return True

    # Check if already downloaded
    if manifest.is_downloaded(message.id):
        existing_name = manifest.get_existing_filename(message.id)
        print(f"[SKIP] Message {message.id}: already downloaded as '{existing_name}'.")
        return True

    # Determine sequence number and destination filename
    seq_num = manifest.get_next_sequence(padding=padding)
    filename = determine_media_filename(message, seq_num=seq_num, padding=padding)
    final_path = os.path.join(dest_folder, filename)

    # Collision guard
    if os.path.exists(final_path):
        base, ext = os.path.splitext(filename)
        filename = f"{base}_msg{message.id}{ext}"
        final_path = os.path.join(dest_folder, filename)

    expected_size_str = format_size(message.file.size) if message.file and message.file.size else "unknown size"
    print(f"\n[DOWNLOAD] Message {message.id} -> '{filename}' ({expected_size_str})")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            success = await download_file_with_verification(message, final_path, client)
            if success:
                actual_size = os.path.getsize(final_path)
                manifest.record_success(message.id, seq_num, filename, actual_size)
                print(f"[SUCCESS] Downloaded: '{filename}' ({format_size(actual_size)})")

                # Respect rate limits with gentle delay
                if DOWNLOAD_DELAY > 0:
                    await asyncio.sleep(DOWNLOAD_DELAY)
                return True

        except FloodWaitError as e:
            wait_time = e.seconds + 2
            print(f"[FLOOD WAIT] Telegram requested a wait of {e.seconds} seconds. Sleeping for {wait_time}s...")
            await asyncio.sleep(wait_time)
            # Re-attempt without incrementing attempt penalty if needed

        except Exception as e:
            err_msg = str(e)
            print(f"[ERROR] Attempt {attempt}/{MAX_RETRIES} failed for message {message.id}: {e}")

            # Handle expired file reference by refreshing message
            if "file reference has expired" in err_msg.lower() or "GetFileRequest" in err_msg or isinstance(e, FileReferenceExpiredError):
                print("  [REFRESH] File reference expired. Fetching fresh message from Telegram...")
                try:
                    refreshed = await client.get_messages(target_entity, ids=message.id)
                    if refreshed:
                        message = refreshed
                except Exception as ref_e:
                    print(f"  [REFRESH FAILED] Could not refresh message: {ref_e}")

            if attempt < MAX_RETRIES:
                backoff = min(2 ** attempt, 15)
                print(f"  Waiting {backoff}s before retry...")
                await asyncio.sleep(backoff)

    # If all attempts exhausted
    print(f"[FAILED] Could not download message {message.id} after {MAX_RETRIES} attempts.")
    manifest.record_failure(message.id, f"Failed after {MAX_RETRIES} attempts")
    with open(FAILED_LOG_FILE, "a", encoding="utf-8") as f_out:
        f_out.write(f"{message.id}\n")

    return False


async def resolve_target_entity(client: TelegramClient, chat_target: Any) -> Any:
    """
    Resolves the Telegram chat or channel entity.
    If not cached locally, fetches user dialogs to ensure access_hash is cached.
    """
    try:
        return await client.get_entity(chat_target)
    except Exception as e:
        print(f"Direct resolution of target '{chat_target}' failed ({e}). Warming entity cache via dialogs...")

    # Fetch recent dialogs to warm Telethon's access_hash cache
    dialogs = await client.get_dialogs(limit=100)
    
    # Try resolving again
    try:
        return await client.get_entity(chat_target)
    except Exception:
        pass

    # Match by ID in fetched dialogs
    target_id_str = str(chat_target).lstrip("-").replace("100", "", 1) if str(chat_target).startswith("-100") else str(chat_target).lstrip("-")
    for d in dialogs:
        d_id_str = str(d.id).lstrip("-").replace("100", "", 1) if str(d.id).startswith("-100") else str(d.id).lstrip("-")
        if d_id_str == target_id_str or str(d.id) == str(chat_target):
            return d.entity

    raise ValueError(f"Could not find or access Telegram chat/channel '{chat_target}'. Please make sure your account has joined this channel/group.")


async def authenticate_if_needed(client: TelegramClient):
    """Checks authentication status; runs interactive prompt if unauthorized."""
    await client.connect()
    if await client.is_user_authorized():
        return

    print("\n=======================================================")
    print(" Telegram session is not authorized. Starting login...")
    print("=======================================================")
    phone = PHONE_NUMBER or input("Enter your phone number (e.g. +351...): ").strip()

    sent = await client.send_code_request(phone)
    code = input("Enter the 5-digit code sent to your Telegram app: ").strip()

    try:
        await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
    except SessionPasswordNeededError:
        import getpass
        pwd = getpass.getpass("Enter your Telegram 2FA cloud password: ")
        await client.sign_in(password=pwd)

    print("Authentication successful! Session saved.\n")


async def main():
    parser = argparse.ArgumentParser(description="Download media from Telegram channel/topic sequentially.")
    parser.add_argument("--channel", type=str, default=None, help="Telegram channel URL or ID (e.g. https://t.me/c/4345651366)")
    parser.add_argument("--start-id", type=int, default=None, help="First message ID to download (inclusive)")
    parser.add_argument("--topic-id", type=str, default=None, help="Forum topic ID to filter by, or 'all' for all topics")
    parser.add_argument("--all-topics", action="store_true", help="Download across all forum topics/sagas in the channel")
    parser.add_argument("--dest", type=str, default=None, help="Destination folder for downloads")
    parser.add_argument("--retry-failed", type=str, nargs="?", const="failed-media.list", default=None, help="Retry downloading messages from failed list file")
    args = parser.parse_args()

    # Validate API credentials
    if not API_ID or not API_HASH:
        print("Error: API_ID and API_HASH must be set in .env file.")
        print("Get them from https://my.telegram.org/apps")
        sys.exit(1)

    try:
        api_id_int = int(API_ID)
    except ValueError:
        print("Error: API_ID must be an integer.")
        sys.exit(1)

    # Resolve target settings
    channel_input = args.channel or CHANNEL_URL or GROUP_ID
    topic_input = args.topic_id if args.topic_id is not None else TOPIC_ID
    start_msg_input = args.start_id if args.start_id is not None else STARTING_MESSAGE_ID
    dest_dir = os.path.abspath(args.dest or DESTINATION_FOLDER)
    use_all_topics = args.all_topics or (topic_input is not None and str(topic_input).strip().lower() in ("all", "none", "*"))

    target_info = parse_telegram_target(
        channel_url=channel_input,
        group_id=GROUP_ID,
        topic_id=topic_input,
        start_msg_id=start_msg_input,
        all_topics=use_all_topics
    )

    chat_id = target_info["chat_id"]
    topic_id = target_info["topic_id"]
    starting_msg_id = target_info["starting_message_id"] or 1

    os.makedirs(dest_dir, exist_ok=True)
    manifest = DownloadManifest(dest_dir, chat_id=chat_id, topic_id=topic_id)

    print("=======================================================")
    print("           Telegram Media Downloader v2.0              ")
    print("=======================================================")
    print(f" Destination Folder:    {dest_dir}")
    print(f" Target Chat ID:        {chat_id}")
    print(f" Topic ID:              {topic_id if topic_id else '(All/None)'}")
    print(f" Starting Message ID:   {starting_msg_id} (inclusive)")
    print(f" Sequence Padding:      {SEQUENCE_PADDING} (e.g. {1:0{SEQUENCE_PADDING}d}_...)")
    print(f" Media Filter:          {MEDIA_TYPES}")
    print(f" Download Delay:        {DOWNLOAD_DELAY}s")
    print("=======================================================\n")

    client = TelegramClient(SESSION_NAME, api_id_int, API_HASH)
    await authenticate_if_needed(client)

    me = await client.get_me()
    print(f"Connected as: {me.first_name} (@{me.username or 'N/A'}, ID: {me.id})")

    # If chat_id not provided, prompt recent dialogs
    if not chat_id:
        print("\nNo channel or GROUP_ID specified. Fetching your recent chats...")
        dialogs = await client.get_dialogs(limit=25)
        chat_dialogs = [d for d in dialogs if d.is_group or d.is_channel]
        for i, d in enumerate(chat_dialogs):
            print(f"  [{i}] {d.name} (ID: {d.id})")
        choice = input("\nEnter group number or ID: ").strip()
        if choice.isdigit() and int(choice) < len(chat_dialogs):
            target_entity = chat_dialogs[int(choice)].entity
        else:
            target_entity = await resolve_target_entity(client, int(choice) if choice.lstrip("-").isdigit() else choice)
    else:
        target_entity = await resolve_target_entity(client, chat_id)

    entity_title = getattr(target_entity, "title", str(chat_id))
    print(f"Target selected: '{entity_title}' (ID: {target_entity.id})")

    # RETRY MODE: If retry-failed specified
    failed_file = args.retry_failed or (sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") and os.path.exists(sys.argv[1]) else None)
    if failed_file and os.path.exists(failed_file):
        print(f"\nRetry mode: Reading failed message IDs from '{failed_file}'...")
        msg_ids = []
        with open(failed_file, "r", encoding="utf-8") as f:
            for line in f:
                m = re.search(r"\b(\d+)\b", line)
                if m:
                    msg_ids.append(int(m.group(1)))
        msg_ids = sorted(list(set(msg_ids)))
        print(f"Found {len(msg_ids)} distinct message IDs to retry.")

        success_count = 0
        for m_id in msg_ids:
            try:
                msg = await client.get_messages(target_entity, ids=m_id)
                if msg:
                    ok = await process_single_message(msg, client, manifest, dest_dir, target_entity, padding=SEQUENCE_PADDING)
                    if ok:
                        success_count += 1
                else:
                    print(f"Message {m_id} not found.")
            except Exception as e:
                print(f"Error fetching message {m_id}: {e}")

        print(f"\nRetry completed: {success_count}/{len(msg_ids)} succeeded.")
        return

    # NORMAL MODE: Iterate messages chronologically from starting_msg_id to newest
    # Note: min_id in Telethon is exclusive, so min_id = max(0, starting_msg_id - 1) ensures message starting_msg_id is included.
    fetch_min_id = max(0, starting_msg_id - 1)
    print(f"\nFetching messages from ID >= {starting_msg_id} in '{entity_title}'...")

    total_media_count = 0
    downloaded_count = 0
    skipped_count = 0
    failed_count = 0

    # If topic_id is specified, use reply_to=topic_id
    iter_kwargs = {
        "reverse": True,
        "min_id": fetch_min_id
    }
    if topic_id:
        iter_kwargs["reply_to"] = topic_id

    try:
        async for message in client.iter_messages(target_entity, **iter_kwargs):
            if message.id < starting_msg_id:
                continue

            if not is_downloadable_media(message, MEDIA_TYPES):
                continue

            total_media_count += 1
            if manifest.is_downloaded(message.id):
                skipped_count += 1
            else:
                ok = await process_single_message(
                    message,
                    client,
                    manifest,
                    dest_dir,
                    target_entity,
                    padding=SEQUENCE_PADDING
                )
                if ok:
                    downloaded_count += 1
                else:
                    failed_count += 1

    except Exception as iter_err:
        print(f"\nError during message retrieval: {iter_err}")
        # If reply_to failed (e.g. topic structure mismatch), fallback to regular iteration
        if topic_id and "reply_to" in iter_kwargs:
            print("Retrying message iteration without 'reply_to' parameter, filtering topic in memory...")
            iter_kwargs.pop("reply_to")
            async for message in client.iter_messages(target_entity, **iter_kwargs):
                if message.id < starting_msg_id:
                    continue
                # Topic filter check in reply_to header
                if message.reply_to:
                    top_id = getattr(message.reply_to, "reply_to_top_id", None) or getattr(message.reply_to, "reply_to_msg_id", None)
                    if top_id != topic_id and message.id != topic_id:
                        continue
                elif message.id != topic_id:
                    continue

                if not is_downloadable_media(message, MEDIA_TYPES):
                    continue

                total_media_count += 1
                if manifest.is_downloaded(message.id):
                    skipped_count += 1
                else:
                    ok = await process_single_message(
                        message,
                        client,
                        manifest,
                        dest_dir,
                        target_entity,
                        padding=SEQUENCE_PADDING
                    )
                    if ok:
                        downloaded_count += 1
                    else:
                        failed_count += 1

    print("\n=======================================================")
    print("                 Download Summary                      ")
    print("=======================================================")
    print(f" Total Media Processed:  {total_media_count}")
    print(f" Newly Downloaded:       {downloaded_count}")
    print(f" Skipped (Completed):    {skipped_count}")
    print(f" Failed:                 {failed_count}")
    print(f" Saved to:               {dest_dir}")
    if failed_count > 0:
        print(f" Failed IDs logged to:   {FAILED_LOG_FILE}")
        print(f" Retry command:          python downloader.py --retry-failed")
    print("=======================================================\n")

    await client.disconnect()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\nDownload interrupted by user. Progress is saved. Re-run to resume.")
        sys.exit(0)
