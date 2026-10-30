import os
import time
import asyncio
import subprocess
from datetime import datetime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from models import Base, User, Stream, Recording

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@db:5432/streamdb")
MEDIAMTX_RTMP = os.getenv("MEDIAMTX_RTMP", "rtmp://mediamtx:1935/live")
STORAGE_DIR = os.getenv("STORAGE_DIR", "/storage")

os.makedirs(f"{STORAGE_DIR}/thumbnails", exist_ok=True)
os.makedirs(f"{STORAGE_DIR}/vods", exist_ok=True)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

def capture_thumbnail(stream_key: str, output_path: str):
    """Знімає 1 кадр із живого RTMP потоку за допомогою ffmpeg CLI"""
    rtmp_url = f"{MEDIAMTX_RTMP}/{stream_key}"
    cmd = [
        "ffmpeg", "-y",
        "-i", rtmp_url,
        "-vframes", "1",
        "-q:v", "2",
        output_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        return True
    except Exception:
        return False

async def worker_loop():
    print("[Transcoder] Worker initialized. Monitoring live streams...")
    while True:
        try:
            async with AsyncSessionLocal() as session:
                # Шукаємо всі стріми зі статусом is_live = True
                res = await session.execute(
                    select(Stream, User).join(User, Stream.user_id == User.id).where(Stream.is_live == True)
                )
                active_streams = res.all()

                for stream, user in active_streams:
                    thumb_name = f"{user.stream_key}.jpg"
                    thumb_path = f"{STORAGE_DIR}/thumbnails/{thumb_name}"
                    
                    # Робимо знімок
                    success = await asyncio.to_thread(capture_thumbnail, user.stream_key, thumb_path)
                    if success:
                        stream.thumbnail_url = f"http://localhost:8000/static/storage/thumbnails/{thumb_name}"
                        await session.commit()
                        print(f"[Transcoder] Thumbnail updated for stream: {user.username}")

        except Exception as e:
            print(f"[Transcoder] Error in loop: {e}")

        await asyncio.sleep(15)

if __name__ == "__main__":
    asyncio.run(worker_loop())