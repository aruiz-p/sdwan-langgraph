"""
This module defines the FastAPI application and the endpoints for the chat API.

It imports necessary modules and functions, sets up logging, 
loads global settings, and initializes the chat agent and the Webex bot manager.

It defines a Pydantic model for the message data and two POST endpoints:
one for sending messages to the chat agent and another for processing alerts.
"""
import uvicorn
import threading
from fastapi import FastAPI
from IPython.display import Image
from logging_config.main import setup_logging
from load_global_settings import (
    HOST_URL,
    LLM_HTTP_PORT,
)
from webex.bot import WebexBotManager
from langchain_core.messages import HumanMessage
from llm_agent import create_agent_graph

from fastapi_models import Message, SnowWebhookMessage


app = FastAPI()
logger = setup_logging()
chat_agent = create_agent_graph()
webex_bot_manager = WebexBotManager()


@app.post("/chat")
def chat_to_llm(message: Message) -> str:
    logger.info(f"MESSAGE_RECEIVED: {message.message}")
    formatted_message = {
        "input": [HumanMessage(content=message.message)],
    }
    result = chat_agent.invoke(formatted_message)
    return result['input'][-1].content

@app.post("/alert")
async def alert(message: SnowWebhookMessage) -> dict:
    """
    This function receives a webhook alert and starts processing it.
    Grafana sends a webhook empty as a keepalive.
    'Firing' is used to identify a real alert.
    """
    logger.info(f"WEBHOOK_MESSAGE_RECEIVED: {message}")
    if message.status.lower() == "firing":
        process_alert(message)
    return {"status": "success"}


def process_alert(message: Message) -> None:
    """
    This function sends the alert to the LLM and sends a notification to the Webex room.
    """
    # logger.info(f"WEBHOOK_SENT_TO_LLM: {message}")
    notification = chat_agent.notification(message)
    notify(notification)


def notify(notification: str) -> None:
    """
    Sends a notification message.
    """
    logger.info(f"SENDING_NOTIFICATION: {notification}")
    webex_bot_manager.send_notification(notification)


if __name__ == "__main__":
    threading.Thread(
        target=uvicorn.run,
        args=("app:app",),
        kwargs={"host": HOST_URL, "port": LLM_HTTP_PORT},
    ).start()
    webex_bot_manager.run()