import asyncio
import json
import logging
import os
import random
from typing import Dict, Any

import httpx
import redis.asyncio as aioredis

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ai_agent")

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "openai/gpt-oss-20b")

GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"

MODERATION_SYSTEM_PROMPT = """
Ти автономний ШІ-модератор чату онлайн-стріму.
Проаналізуй повідомлення користувача на:
1. Прямі грубі образи, розпалювання ворожнечі, нецензурну лайку.
2. Спам, фішинг, рекламу сумнівних посилань.

Відповідай ВИКЛЮЧНО валідним JSON-об'єктом без форматування markdown:
{
  "allowed": true,
  "reason": null,
  "sanitized_text": "текст"
}
Якщо повідомлення неприйнятне: "allowed": false, "reason": "Коротка причина українською".
"""

CHATTER_PROMPTS = [
    "Напиши коротке (до 10 слів) дружнє вітання глядачам стріму українською мовою.",
    "Нагадай чату в одному короткому реченні дотримуватися правил і поважати одне одного.",
    "Спитай у глядачів коротким реченням, як їм якість трансляції або сьогоднішній контент.",
    "Побажай стрімеру вдалого ефіру та гарного настрою одним коротким реченням."
]


async def moderate_message(client: httpx.AsyncClient, text: str) -> Dict[str, Any]:
    """Перевірка тексту через Groq LLM API."""
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY не задано. Пропуск перевірки.")
        return {"allowed": True, "reason": None, "sanitized_text": text}

    payload = {
        "model": MODEL_NAME,
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": MODERATION_SYSTEM_PROMPT},
            {"role": "user", "content": f"Повідомлення: \"{text}\""}
        ]
    }

    try:
        response = await client.post(
            GROQ_ENDPOINT,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=5.0
        )
        if response.status_code != 200:
            logger.error(f"Помилка Groq API (модерація): {response.status_code} - {response.text}")
            return {"allowed": True, "reason": None, "sanitized_text": text}

        content = response.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        logger.error(f"Виняток при виклику LLM API: {e}")
        return {"allowed": True, "reason": None, "sanitized_text": text}


async def answer_user_query(client: httpx.AsyncClient, question: str, user: str) -> str:
    """Генерація відповіді, якщо користувач звернувся до @ai."""
    if not GROQ_API_KEY:
        return f"@{user}, привіт! Я ШІ-асистент трансляції."

    payload = {
        "model": MODEL_NAME,
        "temperature": 0.6,
        "messages": [
            {"role": "system", "content": "Ти помічник на стрімі. Відповідай коротко (1-2 речення), українською, дружелюбно."},
            {"role": "user", "content": f"Користувач {user} запитує: {question}"}
        ]
    }
    try:
        res = await client.post(
            GROQ_ENDPOINT,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json=payload,
            timeout=5.0
        )
        if res.status_code != 200:
            logger.error(f"Помилка Groq API (відповідь): {res.status_code} - {res.text}")
            return f"@{user}, сталася помилка обробки запиту."
        return res.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Помилка генерації відповіді: {e}")
        return f"@{user}, я тут! Гарного перегляду!"


async def run_moderator(redis_client: aioredis.Redis, http_client: httpx.AsyncClient):
    """Слухає сирі повідомлення з чату, модерирує та транслює у відкритий канал."""
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("chat:raw_messages")
    logger.info("ШІ-Агент підписався на чергу 'chat:raw_messages'")

    async for raw in pubsub.listen():
        if raw["type"] != "message":
            continue

        try:
            data = json.loads(raw["data"])
            room_id = data.get("room_id", 1)
            user = data.get("user", "Anonymous")
            text = data.get("text", "")

            decision = await moderate_message(http_client, text)

            if decision.get("allowed", True):
                approved_msg = {
                    "user": user,
                    "text": decision.get("sanitized_text", text),
                    "timestamp": data.get("timestamp")
                }
                await redis_client.publish(f"chat:stream:{room_id}", json.dumps(approved_msg))

                clean_text = text.strip()
                if clean_text.lower().startswith(("@ai", "!ask")):
                    query = clean_text.split(" ", 1)[-1] if " " in clean_text else "Привіт!"
                    reply = await answer_user_query(http_client, query, user)
                    bot_reply = {
                        "user": "AI_BOT 🤖",
                        "text": reply,
                        "is_bot": True
                    }
                    await redis_client.publish(f"chat:stream:{room_id}", json.dumps(bot_reply))

            else:
                reason = decision.get("reason", "Повідомлення заблоковано модератором.")
                system_warn = {
                    "user": "AI_MODERATOR 🛡️",
                    "text": f"@{user}: {reason}",
                    "is_warning": True
                }
                await redis_client.publish(f"chat:stream:{room_id}", json.dumps(system_warn))

        except Exception as ex:
            logger.error(f"Помилка обробки повідомлення: {ex}")


async def periodic_chatter(redis_client: aioredis.Redis, http_client: httpx.AsyncClient, room_id: int = 1):
    """Створює фонову активність бота в чаті."""
    await asyncio.sleep(10)
    while True:
        try:
            if GROQ_API_KEY:
                prompt = random.choice(CHATTER_PROMPTS)
                payload = {
                    "model": MODEL_NAME,
                    "temperature": 0.7,
                    "messages": [
                        {"role": "system", "content": "Ти ввічливий стрім-бот. Відповідай українською, максимум одне лаконічне речення."},
                        {"role": "user", "content": prompt}
                    ]
                }
                res = await http_client.post(
                    GROQ_ENDPOINT,
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=5.0
                )
                if res.status_code == 200:
                    bot_text = res.json()["choices"][0]["message"]["content"].strip()
                    packet = {"user": "AI_BOT 🤖", "text": bot_text, "is_bot": True}
                    await redis_client.publish(f"chat:stream:{room_id}", json.dumps(packet))
                    logger.info(f"AI Chatter: {bot_text}")
                else:
                    logger.error(f"Помилка Groq API (chatter): {res.status_code} - {res.text}")
        except Exception as e:
            logger.error(f"Помилка periodic_chatter: {e}")

        await asyncio.sleep(90)


async def main():
    logger.info(f"Запуск ШІ-агента з моделлю: {MODEL_NAME}")
    redis_client = aioredis.from_url(REDIS_URL, decode_responses=True)
    async with httpx.AsyncClient() as http_client:
        await asyncio.gather(
            run_moderator(redis_client, http_client),
            periodic_chatter(redis_client, http_client)
        )

if __name__ == "__main__":
    asyncio.run(main())