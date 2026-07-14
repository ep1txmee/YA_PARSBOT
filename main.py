import aiohttp
import asyncio
from telegram import Bot
from telegram.error import RetryAfter, TimedOut
from fake_useragent import UserAgent
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
import aiofiles

load_dotenv()

API_KEY = os.getenv('API_KEY')
CHAT_ID = os.getenv('CHAT_ID')

bot = Bot(token=API_KEY)

headers = {'User-Agent': 'TelegramBot (like TwitterBot)'}
SEM_LIMIT = 5  
SEEN_RELEASES_FILE = "seen_releases.txt"  
BASE_DELAY = 2  
MAX_RETRIES = 5 

async def load_seen_releases():
    seen_releases = set()
    try:
        if os.path.exists(SEEN_RELEASES_FILE):
            async with aiofiles.open(SEEN_RELEASES_FILE, "r", encoding="utf-8") as f:
                content = await f.read()
                seen_releases = set(line.strip() for line in content.splitlines() if line.strip())
    except Exception as e:
        print(f"Ошибка при чтении файла {SEEN_RELEASES_FILE}: {e}")
    return seen_releases

async def save_seen_release(release_id):
    try:
        async with aiofiles.open(SEEN_RELEASES_FILE, "a", encoding="utf-8") as f:
            await f.write(f"{release_id}\n")
    except Exception as e:
        print(f"Ошибка при записи релиза {release_id} в файл: {e}")

def find_upcoming_album(data):
    if isinstance(data, dict):
        if 'upcomingAlbum' in data:
            return data['upcomingAlbum']
        for key, value in data.items():
            found = find_upcoming_album(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_upcoming_album(item)
            if found:
                return found
    return None

def format_release_date(release_date_str):
    try:
        release_date = datetime.fromisoformat(release_date_str.replace('Z', '+00:00'))
        release_date += timedelta(days=1)
        return release_date.strftime('%d %B %Y г.')
    except Exception as e:
        print(f"Ошибка форматирования даты: {e}")
        return release_date_str

async def get_upcoming_album(artist_id, session, seen_releases):
    url = f"https://api.music.yandex.net/artists/{artist_id}/brief-info?discographyBlockEnabled=true&useClipDataFormat=true"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                upcoming_album = find_upcoming_album(data)
                if upcoming_album:
                    album_id = str(upcoming_album.get('id'))
                    
                    if album_id in seen_releases:
                        print(f"Релиз {album_id} уже отправлен, пропускаем.")
                        return None
                    
                    title = upcoming_album.get('title')
                    release_date_str = upcoming_album.get('releaseDate')
                    formatted_release_date = format_release_date(release_date_str)
                    cover_uri = upcoming_album.get('coverUri', '').replace('%%', 'orig')
                    artists = upcoming_album.get('artists', [])
                    artist_names = [artist.get('name') for artist in artists]
                    artist_names_str = ', '.join(artist_names)
                    
                    message = (f"{artist_names_str} - {title}\n"
                               f"Дата релиза: {formatted_release_date}\n"
                               f"Айди артиста: {artist_id}\n"
                               f"ID альбома: {album_id}\n\n"
                               f"{cover_uri}")
                    
                    await save_seen_release(album_id)
                    seen_releases.add(album_id)
                    return message
    except Exception as e:
        print(f"Ошибка при запросе артиста {artist_id}: {e}")
    return None

async def send_to_telegram(message, retry_count=0):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message)
        await asyncio.sleep(BASE_DELAY)  
    except RetryAfter as e:
        if retry_count >= MAX_RETRIES:
            print(f"Превышено максимальное количество попыток для сообщения: {message[:50]}...")
            return
        delay = e.retry_after + 1  
        print(f"Флуд-контроль, повтор через {delay} секунд для сообщения: {message[:50]}...")
        await asyncio.sleep(delay)
        await send_to_telegram(message, retry_count + 1)
    except TimedOut:
        if retry_count >= MAX_RETRIES:
            print(f"Тайм-аут при отправке сообщения: {message[:50]}...")
            return
        delay = 2 ** retry_count 
        print(f"Тайм-аут, повтор через {delay} секунд для сообщения: {message[:50]}...")
        await asyncio.sleep(delay)
        await send_to_telegram(message, retry_count + 1)
    except Exception as e:
        print(f"Ошибка отправки сообщения в Telegram: {e}")

async def process_artist_async(line, session, sem, seen_releases, messages):
    parts = line.strip().split(' ')
    if len(parts) < 2:
        print(f"Неверный формат строки: {line.strip()}")
        return

    performer_name = ' '.join(parts[:-1])
    artist_id = parts[-1]

    async with sem:  
        message = await get_upcoming_album(artist_id, session, seen_releases)
        if message:
            messages.append(message)
        else:
            print(f"{performer_name} - не найдено или уже отправлено")

async def main():
    seen_releases = await load_seen_releases()  
    messages = []  

    try:
        async with aiofiles.open('artists.txt', encoding='utf-8') as f:
            lines = await f.readlines()
    except Exception as e:
        print(f"Ошибка при чтении файла artists.txt: {e}")
        return

    sem = asyncio.Semaphore(SEM_LIMIT)  
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [process_artist_async(line, session, sem, seen_releases, messages) for line in lines]
        await asyncio.gather(*tasks)

    print(f"Найдено {len(messages)} новых релизов для отправки.")
    for i, message in enumerate(messages, 1):
        print(f"Отправка сообщения {i}/{len(messages)}...")
        await send_to_telegram(message)

if __name__ == '__main__':
    asyncio.run(main())