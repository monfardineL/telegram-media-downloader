#!/usr/bin/env python3
"""
Authentication helper for Telegram Media Downloader.
Handles login, session checking, and two-factor authentication (2FA).
"""

import os
import sys
import asyncio
import getpass
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PasswordHashInvalidError,
    PhoneNumberInvalidError,
    FloodWaitError
)

load_dotenv()

API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
PHONE_NUMBER = os.getenv("PHONE_NUMBER")
SESSION_NAME = "downloader"


def get_credentials():
    if not API_ID or not API_HASH:
        print("Error: API_ID and API_HASH must be configured in your .env file.")
        print("Get them from https://my.telegram.org/apps")
        sys.exit(1)
    try:
        api_id_int = int(API_ID)
    except ValueError:
        print("Error: API_ID must be an integer.")
        sys.exit(1)
    return api_id_int, API_HASH


async def check_auth_status():
    api_id, api_hash = get_credentials()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    await client.connect()
    
    is_auth = await client.is_user_authorized()
    if is_auth:
        me = await client.get_me()
        print("\n==========================================")
        print("  Telegram Authentication Status: ACTIVE")
        print("==========================================")
        print(f"  Name:     {me.first_name} {me.last_name or ''}".strip())
        print(f"  Username: @{me.username}" if me.username else "  Username: (None)")
        print(f"  User ID:  {me.id}")
        print(f"  Phone:    {me.phone or PHONE_NUMBER or 'N/A'}")
        print("==========================================\n")
    else:
        print("\n==========================================")
        print("  Telegram Authentication Status: NOT LOGGED IN")
        print("==========================================")
        print("  Session exists but is not authorized or expired.")
        print("  Run this script without flags to log in:")
        print("    python auth.py")
        print("==========================================\n")
    
    await client.disconnect()
    return is_auth


async def authenticate():
    api_id, api_hash = get_credentials()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"\nAlready logged in as {me.first_name} ({me.phone or me.username or me.id}).")
        await client.disconnect()
        return True

    print("\nStarting Telegram login flow...")
    phone = PHONE_NUMBER
    if not phone:
        phone = input("Enter your phone number (with country code, e.g. +351...): ").strip()
    else:
        confirm = input(f"Use phone number {phone} from .env? [Y/n]: ").strip().lower()
        if confirm == 'n':
            phone = input("Enter your phone number: ").strip()

    try:
        print(f"Sending login code to {phone}...")
        sent_code = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        print("Error: The phone number is invalid. Please check the country code.")
        await client.disconnect()
        return False
    except FloodWaitError as e:
        print(f"Error: Flood wait active. Telegram asks to wait {e.seconds} seconds before requesting a code.")
        await client.disconnect()
        return False
    except Exception as e:
        print(f"Failed to send code request: {e}")
        await client.disconnect()
        return False

    code = input("Enter the 5-digit code sent to your Telegram app (or SMS): ").strip()

    try:
        await client.sign_in(phone, code, phone_code_hash=sent_code.phone_code_hash)
    except PhoneCodeInvalidError:
        print("Error: The code entered is invalid.")
        await client.disconnect()
        return False
    except SessionPasswordNeededError:
        print("Two-step verification (2FA) is enabled on this account.")
        password = getpass.getpass("Enter your Telegram 2FA cloud password: ")
        try:
            await client.sign_in(password=password)
        except PasswordHashInvalidError:
            print("Error: Incorrect 2FA password.")
            await client.disconnect()
            return False

    me = await client.get_me()
    print("\nAuthentication successful!")
    print(f"Logged in as: {me.first_name} (@{me.username or 'no_username'}, ID: {me.id})")
    print("Session saved to 'downloader.session'. You can now run 'python downloader.py'.\n")
    await client.disconnect()
    return True


async def logout():
    api_id, api_hash = get_credentials()
    client = TelegramClient(SESSION_NAME, api_id, api_hash)
    await client.connect()
    if await client.is_user_authorized():
        await client.log_out()
        print("Successfully logged out from Telegram.")
    else:
        print("Already logged out.")
    await client.disconnect()
    if os.path.exists(f"{SESSION_NAME}.session"):
        try:
            os.remove(f"{SESSION_NAME}.session")
            print("Session file deleted.")
        except Exception:
            pass


def main():
    if "--check" in sys.argv:
        asyncio.run(check_auth_status())
    elif "--logout" in sys.argv:
        asyncio.run(logout())
    else:
        asyncio.run(authenticate())


if __name__ == "__main__":
    main()
