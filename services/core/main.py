import os
from typing import Optional, List
from datetime import datetime

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, select
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# --- НАЛАШТУВАННЯ БАЗИ ДАНИХ ---
DATABASE_URL = os.getenv(
    "DATABASE_URL", 
    "postgresql+asyncpg://postgres:postgres@db:5432/streamdb"
)

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

# --- МОДЕЛІ ДАНИХ ---
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False)
    email = Column(String(100), unique=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    stream_key = Column(String(64), unique=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    streams = relationship("Stream", back_populates="owner")

class Stream(Base):
    __tablename__ = "streams"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100), nullable=False)
    description = Column(String(255), nullable=True)
    is_live = Column(Boolean, default=False)
    viewers_count = Column(Integer, default=0)
    thumbnail_url = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"))

    owner = relationship("User", back_populates="streams")

# --- СХЕМИ PYDANTIC ---
class MediaMTXAuthPayload(BaseModel):
    action: Optional[str] = None
    path: Optional[str] = None
    user: Optional[str] = None
    password: Optional[str] = None
    ip: Optional[str] = None

class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str

# --- ІНІЦІАЛІЗАЦІЯ FASTAPI ---
app = FastAPI(title="StreamHub Core API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Монтування тома для картинок і записів
STORAGE_DIR = "/app/storage"
os.makedirs(os.path.join(STORAGE_DIR, "thumbnails"), exist_ok=True)
os.makedirs(os.path.join(STORAGE_DIR, "recordings"), exist_ok=True)
app.mount("/static/storage", StaticFiles(directory=STORAGE_DIR), name="storage")

@app.on_event("startup")
async def startup_event():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# --- ЕНДПОІНТИ АВТОРИЗАЦІЇ ТА КОРИСТУВАЧІВ ---
@app.post("/api/register")
async def register(data: UserRegister, db: AsyncSession = Depends(get_db)):
    # Перевірка на дублікат
    res = await db.execute(select(User).where((User.username == data.username) | (User.email == data.email)))
    if res.scalars().first():
        raise HTTPException(status_code=400, detail="User already exists")

    import secrets
    stream_key = f"live_{secrets.token_hex(12)}"
    
    # Спрощене збереження (або хешування через bcrypt за наявності)
    new_user = User(
        username=data.username,
        email=data.email,
        hashed_password=data.password,
        stream_key=stream_key
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    # Створюємо відповідний канал
    new_stream = Stream(
        title=f"{new_user.username}'s Broadcast",
        description="Low-latency microservices stream",
        user_id=new_user.id
    )
    db.add(new_stream)
    await db.commit()

    return {
        "status": "ok",
        "user_id": new_user.id,
        "username": new_user.username,
        "stream_key": new_user.stream_key
    }

# --- ЕНДПОІНТИ СТРІМІВ ---
@app.get("/api/streams")
async def get_streams(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Stream, User)
        .join(User, Stream.user_id == User.id)
    )
    rows = result.all()
    items = []
    for stream, user in rows:
        items.append({
            "id": stream.id,
            "title": stream.title,
            "description": stream.description,
            "is_live": stream.is_live,
            "viewers_count": stream.viewers_count,
            "playback_url": f"http://localhost:8888/live/{user.stream_key}/index.m3u8",
            "thumbnail_url": stream.thumbnail_url or f"http://localhost:8000/static/storage/thumbnails/{user.stream_key}.jpg"
        })
    return {"items": items}

# --- ВЕБХУКИ ДЛЯ MEDIAMTX (ЗАХИСТ RTMP) ---
@app.post("/api/streams/auth")
async def authenticate_stream(payload: MediaMTXAuthPayload, db: AsyncSession = Depends(get_db)):
    raw_path = payload.path or ""
    stream_key = raw_path.split("/")[-1].strip()

    if not stream_key:
        raise HTTPException(status_code=400, detail="Stream key missing")

    # Валідація: шукаємо користувача з таким ключем у БД
    result = await db.execute(select(User).where(User.stream_key == stream_key))
    user = result.scalars().first()

    if not user:
        # 401 змусить MediaMTX скинути з'єднання з OBS
        raise HTTPException(status_code=401, detail="Invalid stream key")

    # Вмикаємо статус LIVE
    stream_result = await db.execute(select(Stream).where(Stream.user_id == user.id))
    stream = stream_result.scalars().first()
    if stream:
        stream.is_live = True
        await db.commit()

    return {"status": "authorized"}

@app.post("/api/streams/unpublish")
async def on_stream_unpublish(payload: MediaMTXAuthPayload, db: AsyncSession = Depends(get_db)):
    raw_path = payload.path or ""
    stream_key = raw_path.split("/")[-1].strip()

    result = await db.execute(select(User).where(User.stream_key == stream_key))
    user = result.scalars().first()

    if user:
        stream_result = await db.execute(select(Stream).where(Stream.user_id == user.id))
        stream = stream_result.scalars().first()
        if stream:
            stream.is_live = False
            await db.commit()

    return {"status": "unregistered"}