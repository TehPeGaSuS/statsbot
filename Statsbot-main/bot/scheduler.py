"""
bot/scheduler.py
Periodic task scheduler:
- every minute: fire sensors.on_minute()
- at midnight: daily reset + optional word reset
- on Monday midnight: weekly reset
- on 1st of month midnight: monthly reset
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta

log = logging.getLogger("scheduler")


class Scheduler:
    def __init__(self, sensors_list, connector_list, config):
        """
        sensors_list: list of Sensors instances (one per network)
        connector_list: list of IRCConnector instances
        """
        self.sensors_list = sensors_list
        self.connectors = connector_list
        self.cfg = config
        self._running = False
        self._last_day = None
        self._last_week = None
        self._last_month = None

    async def run(self):
        self._running = True
        log.info("Scheduler started.")
        while self._running:
            await asyncio.sleep(60)
            now = datetime.now()
            try:
                self._tick(now)
            except Exception as e:
                log.error(f"Scheduler tick error: {e}", exc_info=True)

    def _tick(self, now: datetime):
        # Minutely: fire on_minute for all networks
        for sensors, connector in zip(self.sensors_list, self.connectors):
            members = connector.get_channel_members()
            sensors.on_minute(members)

        # Daily reset at midnight
        today = now.date()
        if self._last_day != today and now.hour == 0:
            for sensors in self.sensors_list:
                sensors.on_daily_reset()
            self._last_day = today

        # Weekly reset on Monday
        weekday = now.weekday()  # Monday=0
        week = now.isocalendar()[1]
        if weekday == 0 and self._last_week != week and now.hour == 0:
            for sensors in self.sensors_list:
                sensors.on_weekly_reset()
            self._last_week = week

        # Monthly reset on 1st
        month = (now.year, now.month)
        if now.day == 1 and self._last_month != month and now.hour == 0:
            for sensors in self.sensors_list:
                sensors.on_monthly_reset()
            self._last_month = month

        log.debug(f"Tick at {now.strftime('%H:%M')}")

    def stop(self):
        self._running = False
