import asyncio
import logging
from typing import Optional
import aiohttp

logger = logging.getLogger("TwitchDrops.ntfy")


class NtfyNotification:
    def __init__(self, topic: str, server: str = "https://ntfy.sh", enabled: bool = False):
        self.topic = topic
        self.server = server.rstrip('/')  # Remove trailing slash if present
        self.enabled = enabled

    async def send(self, message: str, title: Optional[str] = None, priority: str = "default", tags: Optional[list] = None):
        """
        Send a notification to ntfy
        
        Args:
            message: The notification message body
            title: Optional title for the notification
            priority: Priority level (min, low, default, high, emergency)
            tags: Optional list of emoji tags
        """
        if not self.enabled or not self.topic:
            logger.debug("Ntfy notifications disabled or topic not set, skipping notification")
            return False
            
        try:
            url = f"{self.server}/{self.topic}"
            
            headers = {
                "Content-Type": "text/plain; charset=utf-8",
            }
            
            if title:
                headers["Title"] = title
            if priority:
                headers["Priority"] = priority
            if tags:
                headers["Tags"] = ",".join(tags)
                
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=message.encode('utf-8'), headers=headers) as response:
                    if response.status == 200:
                        logger.info(f"Ntfy notification sent successfully: {title or message[:50]}...")
                        return True
                    else:
                        logger.error(f"Failed to send ntfy notification: HTTP {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Error sending ntfy notification: {e}")
            return False

    def update_config(self, topic: str, server: str = "https://ntfy.sh", enabled: bool = False):
        """Update ntfy configuration"""
        self.topic = topic
        self.server = server.rstrip('/')
        self.enabled = enabled