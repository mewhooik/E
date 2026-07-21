#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pinnacle Ebook Merged PDF Bot - Standalone (Heroku/Server Ready)
"""

import os, re, asyncio, logging
from datetime import datetime
from typing import List
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pypdf import PdfWriter
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

# ───────────────── CONFIG ─────────────────
# ✅ अपना Bot Token, API ID और API Hash यहाँ डालें
BOT_TOKEN = "8646009620:AAFnz0TBeN675UJX52GQq5rEXvsWa-RWfvI"
API_ID = 22370234       # अपना API ID डालें
API_HASH = "706badded011715ae115e5ab3bf83f87" # अपना API Hash डालें

APP_NAME = "Pinnacle Ebook"
EBOOKS_API = "https://auth.ssccglpinnacle.com/api/ebooksforactive?active=true"
EBOOK_CHAPTERS_API = "https://auth.ssccglpinnacle.com/api/chapters-ebook/{book_id}"
EBOOK_PDFS_API = "https://auth.ssccglpinnacle.com/api/pdfs-ebook/{chapter_id}"
CLOUDFRONT_BASE = "https://dzdx39zg243ni.cloudfront.net/{s3_key}"
OUTPUT_DIR = "downloads"

# Pinnacle Auth Token
AUTH_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjY5NWI0MmJjNzQwZGFkMjQzN2I1NzhlYiIsInJvbGUiOiJzdHVkZW50IiwiaXAiOiIxNTIuNTkuMTcuOTAiLCJkZXZpY2UiOiJNb3ppbGxhLzUuMCAoV2luZG93cyBOVCAxMC4wOyBXaW42NDsgeDY0KSBBcHBsZVdlYktpdC81MzcuMzYgKEtIVE1MLCBsaWtlIEdlY2tvKSBDaHJvbWUvMTUwLjAuMC4wIFNhZmFyaS81MzcuMzYiLCJpYXQiOjE3ODQxNzg2MzYsImV4cCI6MTg0NzI1MDYzNn0.z4e1LKkpvkxCvjqlipVg_wrwffeCt4dZidr6yuLfy6o"

HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://ebooks.ssccglpinnacle.com",
    "referer": "https://ebooks.ssccglpinnacle.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
    "authorization": f"Bearer {AUTH_TOKEN}"
}

logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
log = logging.getLogger("PinnacleBot")

# ───────────────── HELPERS ─────────────────
def create_session():
    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[401, 403, 429, 500, 502, 503, 504])
    adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(HEADERS)
    return session

def fetch_json(url, timeout=15):
    try:
        sess = create_session()
        r = sess.get(url, timeout=timeout)
        if r.status_code in [401, 403]:
            log.error(f"{r.status_code} Forbidden/Unauthorized: {url}")
            return None
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"fetch_json error: {e}")
        return None

def sanitize(name):
    if not name: return "ebook"
    name = re.sub(r'[\\/*?:"<>|]', "", str(name))
    name = re.sub(r'\s+', '_', name.strip())
    return name[:80] or "ebook"

def truncate(title, n=50):
    if not title: return ""
    title = str(title).strip()
    return title if len(title) <= n else title[:n-3] + "..."

def get_price(book):
    price = book.get("price")
    if isinstance(price, (int, float)) and price > 0:
        return int(price)
    return 0

def is_free(book):
    return book.get("price", 0) == 0 or book.get("isFree") == True

def parse_user_input(inp: str, total_books: int) -> List[int]:
    if not inp: return []
    indices = []
    parts = inp.replace(" ", "").split(",")
    for part in parts:
        if "-" in part:
            try:
                a, b = map(int, part.split("-"))
                indices.extend(range(max(1, a), min(total_books, b) + 1))
            except: continue
        else:
            try:
                idx = int(part)
                if 1 <= idx <= total_books:
                    indices.append(idx)
            except: continue
    return sorted(list(set(indices)))

# ───────────────── API FUNCTIONS ─────────────────
def get_all_books():
    data = fetch_json(EBOOKS_API)
    return data if isinstance(data, list) else []

def get_chapters(book_id):
    url = EBOOK_CHAPTERS_API.format(book_id=book_id)
    data = fetch_json(url, timeout=12)
    if isinstance(data, list):
        return sorted(data, key=lambda x: x.get("sequence", 999))
    return []

def get_chapter_pdf(chapter_id):
    url = EBOOK_PDFS_API.format(chapter_id=chapter_id)
    data = fetch_json(url, timeout=8)
    return data[0] if isinstance(data, list) and data else None

# ───────────────── CORE: DOWNLOAD & MERGE ─────────────────
async def download_and_merge_pdf(book, chat_id, bot, progress_msg_id):
    """Downloads chapters, merges them, and updates progress message."""
    book_id = book.get("_id")
    full_title = book.get("title", "Unknown")
    chapters = get_chapters(book_id)
    
    if not chapters:
        return None, 0, 0
    
    safe_title = sanitize(full_title)
    final_pdf_path = os.path.join(OUTPUT_DIR, f"{safe_title}_Merged.pdf")
    
    pdf_writer = PdfWriter()
    pdf_count = 0
    total_ch = len(chapters)
    
    log.info(f"Starting merge for: {full_title}")
    
    for idx, ch in enumerate(chapters, 1):
        ch_title = ch.get("title", "Unknown")
        ch_id = ch.get("_id")
        
        # Update Progress Message
        percent = int((idx / total_ch) * 100)
        progress_text = (
            f"⏳ <b>Processing:</b> {truncate(full_title, 40)}\n"
            f"📥 Merged: <b>{idx}/{total_ch}</b> Chapters ({percent}%)\n"
            f"🔄 Current: {truncate(ch_title, 35)}\n"
            f"<i>Please wait, do not spam...</i>"
        )
        try:
            await bot.edit_message_text(chat_id, progress_msg_id, progress_text)
        except Exception:
            pass # Ignore if message was deleted or flood wait
        
        pdf_info = get_chapter_pdf(ch_id)
        if pdf_info and pdf_info.get("s3Key"):
            s3_key = pdf_info.get("s3Key")
            pdf_url = CLOUDFRONT_BASE.format(s3_key=s3_key)
            temp_pdf_path = os.path.join(OUTPUT_DIR, f"temp_{ch_id}.pdf")
            
            try:
                sess = create_session()
                with sess.get(pdf_url, stream=True, timeout=60) as r:
                    r.raise_for_status()
                    with open(temp_pdf_path, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                
                pdf_writer.append(temp_pdf_path)
                pdf_count += 1
                
                # Delete temp file immediately to save Heroku/Server disk space
                if os.path.exists(temp_pdf_path):
                    os.remove(temp_pdf_path)
                    
            except Exception as e:
                log.error(f"Failed to download/merge {ch_title}: {e}")
                if os.path.exists(temp_pdf_path):
                    os.remove(temp_pdf_path)
            
            await asyncio.sleep(0.1) # Prevent rate limiting
            
    # Save final merged PDF
    if pdf_count > 0:
        with open(final_pdf_path, "wb") as f_out:
            pdf_writer.write(f_out)
        log.info(f"Successfully merged {pdf_count}/{total_ch} chapters into {final_pdf_path}")
        return final_pdf_path, pdf_count, total_ch
    else:
        log.warning(f"No PDFs could be downloaded for {full_title}")
        return None, 0, total_ch

# ───────────────── PYROGRAM BOT SETUP ─────────────────
app = Client("PinnacleMergedBot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

@app.on_message(filters.command("start") & filters.private)
async def start_command(client: Client, message: Message):
    chat_id = message.chat.id
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    
    wait_msg = await message.reply_text("🔄 Fetching available batches... Please wait.")
    
    books = await asyncio.to_thread(get_all_books)
    if not books:
        return await wait_msg.edit_text("❌ Failed to fetch ebooks! Check API connection or Token.")
    
    total = len(books)
    await wait_msg.delete()
    
    # Create list file
    list_file = os.path.join(OUTPUT_DIR, "Pinnacle_Ebooks_List.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        f.write("📚 Pinnacle Available Ebooks\n\n")
        for i, book in enumerate(books, 1):
            title = truncate(book.get("title", "Unknown"), 60)
            price_tag = "🟡 Free" if is_free(book) else f"₹{get_price(book)}"
            f.write(f"{i}] {title} ({price_tag})\n")
    
    caption = (
        f"📚 <b>Total Available Batches: {total}</b>\n\n"
        f"👇 <b>Reply with book number(s) to get Merged PDF:</b>\n\n"
        f"<b>Examples:</b>\n"
        f"• <code>5</code> → Only book #5\n"
        f"• <code>1,3,5</code> → Books 1, 3, and 5\n"
        f"• <code>10-15</code> → Books 10 to 15\n\n"
        f"<i>⚠️ Note: Large books may take 1-3 minutes to merge.</i>"
    )
    
    await message.reply_document(
        document=list_file,
        file_name="Pinnacle_Ebooks_List.txt",
        caption=caption
    )
    if os.path.exists(list_file):
        os.remove(list_file)

@app.on_message(filters.text & filters.private & ~filters.command("start"))
async def handle_selection(client: Client, message: Message):
    chat_id = message.chat.id
    user_input = message.text.strip()
    
    # Fetch books again to ensure fresh data
    books = await asyncio.to_thread(get_all_books)
    if not books:
        return await message.reply_text("❌ Failed to fetch books. Try /start again.")
    
    total = len(books)
    indices = parse_user_input(user_input, total)
    
    if not indices:
        return await message.reply_text("❌ Invalid input! Use formats like: `1`, `1,3,5`, or `10-15`")
    
    # Acknowledge request
    ack_msg = await message.reply_text(f"✅ Received! Processing {len(indices)} book(s). This will take some time...")
    await asyncio.sleep(1)
    await ack_msg.delete()
    
    success_count = 0
    
    for idx in indices:
        if idx > len(books): continue
        book = books[idx - 1]
        full_title = book.get("title", "Unknown")
        book_id = book.get("_id")
        price = get_price(book) if not is_free(book) else "Free"
        image_url = book.get("image", "")
        
        # 1. Send Photo + Details First
        details_caption = (
            f"📚 <b>{full_title}</b>\n\n"
            f"🪪 <b>ID:</b> <code>{book_id}</code>\n"
            f"💸 <b>Price:</b> ₹{price}\n"
            f"📂 <b>Status:</b> Preparing Merged PDF..."
        )
        
        try:
            if image_url and image_url.startswith("http"):
                await client.send_photo(chat_id, photo=image_url, caption=details_caption)
            else:
                await client.send_message(chat_id, details_caption)
        except Exception as e:
            log.error(f"Failed to send photo: {e}")
            await client.send_message(chat_id, details_caption)
        
        # 2. Send Progress Message
        progress_msg = await client.send_message(
            chat_id, 
            f"⏳ <b>Initializing merge for:</b> {truncate(full_title, 40)}\n🔄 Please wait..."
        )
        
        # 3. Download & Merge
        merged_pdf_path, pdf_cnt, ch_cnt = await download_and_merge_pdf(book, chat_id, client, progress_msg.id)
        
        if not merged_pdf_path:
            await progress_msg.edit_text(f"❌ Failed to merge <b>{full_title}</b>. No chapters found or API error.")
            await asyncio.sleep(2)
            continue
        
        # 4. Upload Final Merged PDF
        final_caption = (
            f"✅ <b>Merge Complete!</b>\n\n"
            f"📚 <b>Book:</b> {full_title}\n"
            f"📄 <b>Chapters Merged:</b> {pdf_cnt} / {ch_cnt}\n"
            f"📁 <b>File:</b> <code>{os.path.basename(merged_pdf_path)}</code>\n\n"
            f"<i>Powered by Pinnacle Ebook Bot</i>"
        )
        
        try:
            await client.send_document(
                chat_id=chat_id,
                document=merged_pdf_path,
                file_name=os.path.basename(merged_pdf_path),
                caption=final_caption
            )
            success_count += 1
        except FloodWait as e:
            await client.send_message(chat_id, f"⏳ FloodWait: Waiting for {e.value} seconds...")
            await asyncio.sleep(e.value)
            await client.send_document(chat_id=chat_id, document=merged_pdf_path, caption=final_caption)
            success_count += 1
        except Exception as e:
            log.error(f"Failed to upload PDF: {e}")
            await client.send_message(chat_id, f"❌ Failed to upload {full_title}. Error: {e}")
        
        # 5. Delete Progress Message
        try:
            await progress_msg.delete()
        except Exception:
            pass
        
        # 6. Clean up merged PDF from server to save space
        if os.path.exists(merged_pdf_path):
            os.remove(merged_pdf_path)
            
        # Small delay between books to prevent API rate limits
        await asyncio.sleep(1.5)
    
    # Final Summary
    await message.reply_text(
        f"🎉 <b>Task Completed!</b>\n\n"
        f"✅ Successfully processed and sent: <b>{success_count}</b> book(s).\n"
        f"❌ Failed/Skipped: <b>{len(indices) - success_count}</b> book(s)."
    )

# ───────────────── RUN BOT ─────────────────
if __name__ == "__main__":
    log.info("🚀 Starting Pinnacle Merged PDF Bot...")
    app.run()
