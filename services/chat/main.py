import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Dict, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
import redis.asyncio as aioredis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("chat_service")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

app = FastAPI(title="StreamHub Chat Service", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ConnectionManager:
    def __init__(self):
        # Зберігаємо підключені сокети за room_id: Dict[int, Set[WebSocket]]
        self.active_rooms: Dict[int, Set[WebSocket]] = {}
        self.redis: aioredis.Redis | None = None
        self.listener_tasks: Dict[int, asyncio.Task] = {}

    async def init_redis(self):
        if not self.redis:
            self.redis = aioredis.from_url(REDIS_URL, decode_responses=True)

    async def connect(self, websocket: WebSocket, room_id: int):
        await websocket.accept()
        if room_id not in self.active_rooms:
            self.active_rooms[room_id] = set()
            # Запускаємо слухача каналу публічних повідомлень для цієї кімнати
            self.listener_tasks[room_id] = asyncio.create_task(self.listen_room_channel(room_id))
        self.active_rooms[room_id].add(websocket)
        logger.info(f"Клієнт приєднався до кімнати {room_id}. Загалом у кімнаті: {len(self.active_rooms[room_id])}")

    def disconnect(self, websocket: WebSocket, room_id: int):
        if room_id in self.active_rooms:
            self.active_rooms[room_id].discard(websocket)
            if not self.active_rooms[room_id]:
                del self.active_rooms[room_id]
                # Зупиняємо підписку Redis, якщо в кімнаті нікого немає
                task = self.listener_tasks.pop(room_id, None)
                if task:
                    task.cancel()
        logger.info(f"Клієнт від'єднався від кімнати {room_id}")

    async def broadcast_to_room(self, room_id: int, message: dict):
        """Розсилає повідомлення усім активним сокетам у кімнаті."""
        sockets = self.active_rooms.get(room_id, set()).copy()
        for connection in sockets:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"Помилка надсилання у сокет: {e}")
                self.disconnect(connection, room_id)

    async def listen_room_channel(self, room_id: int):
        """Підписка на перевірені повідомлення, які пройшли ШІ-модерацію."""
        r = aioredis.from_url(REDIS_URL, decode_responses=True)
        pubsub = r.pubsub()
        channel = f"chat:stream:{room_id}"
        await pubsub.subscribe(channel)
        logger.info(f"Chat-service підписався на перевірений канал: {channel}")

        try:
            async for raw in pubsub.listen():
                if raw["type"] == "message":
                    data = json.loads(raw["data"])
                    await self.broadcast_to_room(room_id, data)
        except asyncio.CancelledError:
            await pubsub.unsubscribe(channel)
            await r.close()


manager = ConnectionManager()

@app.on_event("startup")
async def startup():
    await manager.init_redis()

@app.get("/health")
async def health():
    return {"status": "ok", "service": "chat"}

@app.websocket("/ws/chat/{room_id}")
async def chat_websocket(
    websocket: WebSocket,
    room_id: int,
    username: str = Query("Guest")
):
    await manager.connect(websocket, room_id)
    try:
        while True:
            data_text = await websocket.receive_text()
            try:
                payload = json.loads(data_text)
                text = payload.get("text", "").strip()
            except json.JSONDecodeError:
                text = data_text.strip()

            if not text:
                continue

            # Відправляємо повідомлення в чергу сирих повідомлень для ШІ-агента
            raw_event = {
                "room_id": room_id,
                "user": username,
                "text": text,
                "timestamp": datetime.utcnow().strftime("%H:%M:%S")
            }
            await manager.redis.publish("chat:raw_messages", json.dumps(raw_event))
            logger.info(f"Повідомлення від {username} надіслано на ШІ-перевірку: {text}")

    except WebSocketDisconnect:
        manager.disconnect(websocket, room_id)
    except Exception as e:
        logger.error(f"Помилка у сокет-з'єднанні: {e}")
        manager.disconnect(websocket, room_id)