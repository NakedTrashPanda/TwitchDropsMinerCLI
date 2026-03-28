from __future__ import annotations

import sys
import signal
import asyncio
import logging
import argparse
import traceback
from typing import NoReturn, Any, List
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

import truststore
truststore.inject_into_ssl()

from translate import _
from tui import TuiManager
from twitch import Twitch, Channel
from settings import Settings
from version import __version__
from exceptions import CaptchaRequired, ExitRequest, ReloadRequest
from utils import lock_file
from constants import LOGGING_LEVELS, FILE_FORMATTER, LOG_PATH, LOCK_PATH, State, WebsocketTopic, PriorityMode, MAX_INT, MAX_CHANNELS
from channel import Game


logger = logging.getLogger("TwitchDrops")


class HeadlessTwitch(Twitch):
    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.gui = TuiManager(self)
        self.gui.start()

    def _create_gui(self):
        pass

    def print(self, message: str):
        self.gui.print(message)

    def close(self):
        self.change_state(State.EXIT)
        self.gui.close()
    
    def watch(self, channel: Channel, *, update_status: bool = True):
        self.watching_channel.set(channel)
        if update_status:
            status_text = _("status", "watching").format(channel=channel.name)
            self.print(status_text)

    def stop_watching(self):
        self.watching_channel.clear()

    async def _run(self):
        auth_state = await self.get_auth()
        await self.websocket.start()
        # NOTE: watch task is explicitly restarted on each new run
        if self._watching_task is not None:
            self._watching_task.cancel()
        self._watching_task = asyncio.create_task(self._watch_loop())
        # Add default topics
        self.websocket.add_topics([
            WebsocketTopic("User", "Drops", auth_state.user_id, self.process_drops),
            WebsocketTopic(
                "User", "Notifications", auth_state.user_id, self.process_notifications
            ),
        ])
        full_cleanup: bool = False
        channels: final[OrderedDict[int, Channel]] = self.channels
        self.change_state(State.INVENTORY_FETCH)
        while True:
            if self._state is State.IDLE:
                if self.settings.dump:
                    self.gui.close()
                    continue
                logger.info(_("gui", "status", "idle"))
                self.stop_watching()
                # clear the flag and wait until it's set again
                self._state_change.clear()
            elif self._state is State.INVENTORY_FETCH:
                # ensure the websocket is running
                await self.websocket.start()
                await self.fetch_inventory()
                # Save state on every inventory fetch
                self.save()
                self.change_state(State.GAMES_UPDATE)
            elif self._state is State.GAMES_UPDATE:
                # claim drops from expired and active campaigns
                for campaign in self.inventory:
                    if not campaign.upcoming:
                        for drop in campaign.drops:
                            if drop.can_claim:
                                await drop.claim()
                # figure out which games we want
                self.wanted_games.clear()
                exclude = self.settings.exclude
                priority = self.settings.priority
                priority_mode = self.settings.priority_mode
                priority_only = priority_mode is PriorityMode.PRIORITY_ONLY

                next_hour = datetime.now(timezone.utc) + timedelta(hours=1)
                sorted_campaigns: list[DropsCampaign] = self.inventory
                if not priority_only:
                    if priority_mode is PriorityMode.ENDING_SOONEST:
                        sorted_campaigns.sort(key=lambda c: c.ends_at)
                    elif priority_mode is PriorityMode.LOW_AVBL_FIRST:
                        sorted_campaigns.sort(key=lambda c: c.availability)
                sorted_campaigns.sort(
                    key=lambda c: (
                        priority.index(c.game.name) if c.game.name in priority else MAX_INT
                    )
                )
                for campaign in sorted_campaigns:
                    game: Game = campaign.game

                    if (
                        game not in self.wanted_games  # isn't already there
                        # and isn't excluded by list or priority mode
                        and game.name not in exclude
                        and (not priority_only or game.name in priority)
                        # and can be progressed within the next hour
                        and not campaign.finished and campaign.can_earn_within(next_hour)
                    ):
                        # non-excluded games with no priority are placed last, below priority ones
                        self.wanted_games.append(game)
                full_cleanup = True
                self.restart_watching()
                self.change_state(State.CHANNELS_CLEANUP)
            elif self._state is State.CHANNELS_CLEANUP:
                logger.info(_("gui", "status", "cleanup"))
                if not self.wanted_games or full_cleanup:
                    # no games selected or we're doing full cleanup: remove everything
                    to_remove_channels: list[Channel] = list(channels.values())
                else:
                    # remove all channels that:
                    to_remove_channels = [
                        channel
                        for channel in channels.values()
                        if (
                            not channel.acl_based  # aren't ACL-based
                            and (
                                channel.offline  # and are offline
                                # or online but aren't streaming the game we want anymore
                                or (channel.game is None or channel.game not in self.wanted_games)
                            )
                        )
                    ]
                full_cleanup = False
                if to_remove_channels:
                    to_remove_topics: list[str] = []
                    for channel in to_remove_channels:
                        to_remove_topics.append(
                            WebsocketTopic.as_str("Channel", "StreamState", channel.id)
                        )
                        to_remove_topics.append(
                            WebsocketTopic.as_str("Channel", "StreamUpdate", channel.id)
                        )
                    self.websocket.remove_topics(to_remove_topics)
                    for channel in to_remove_channels:
                        del channels[channel.id]
                if self.wanted_games:
                    self.change_state(State.CHANNELS_FETCH)
                else:
                    # with no games available, we switch to IDLE after cleanup
                    self.print(_("status", "no_campaign"))
                    self.change_state(State.IDLE)
            elif self._state is State.CHANNELS_FETCH:
                logger.info(_("gui", "status", "gathering"))
                # start with all current channels, clear the memory
                new_channels: set[Channel] = set(channels.values())
                channels.clear()
                # gather and add ACL channels from campaigns
                no_acl: set[Game] = set()
                acl_channels: set[Channel] = set()
                next_hour = datetime.now(timezone.utc) + timedelta(hours=1)
                for campaign in self.inventory:
                    if (
                        campaign.game in self.wanted_games
                        and campaign.can_earn_within(next_hour)
                    ):
                        if campaign.allowed_channels:
                            acl_channels.update(campaign.allowed_channels)
                        else:
                            no_acl.add(campaign.game)
                acl_channels.difference_update(new_channels)
                await self.bulk_check_online(acl_channels)
                new_channels.update(acl_channels)
                for game in no_acl:
                    new_channels.update(await self.get_live_streams(game, drops_enabled=True))
                
                ordered_channels: list[Channel] = sorted(
                    new_channels, key=self._viewers_key, reverse=True
                )
                ordered_channels.sort(key=lambda ch: ch.acl_based, reverse=True)
                ordered_channels.sort(key=self.get_priority)
                
                to_remove_channels = ordered_channels[MAX_CHANNELS:]
                ordered_channels = ordered_channels[:MAX_CHANNELS]
                if to_remove_channels:
                    to_remove_topics = []
                    for channel in to_remove_channels:
                        to_remove_topics.append(
                            WebsocketTopic.as_str("Channel", "StreamState", channel.id)
                        )
                        to_remove_topics.append(
                            WebsocketTopic.as_str("Channel", "StreamUpdate", channel.id)
                        )
                    self.websocket.remove_topics(to_remove_topics)

                for channel in ordered_channels:
                    channels[channel.id] = channel
                
                to_add_topics: list[WebsocketTopic] = []
                for channel_id in channels:
                    to_add_topics.append(
                        WebsocketTopic(
                            "Channel", "StreamState", channel_id, self.process_stream_state
                        )
                    )
                    to_add_topics.append(
                        WebsocketTopic(
                            "Channel", "StreamUpdate", channel_id, self.process_stream_update
                        )
                    )
                self.websocket.add_topics(to_add_topics)
                
                watching_channel = self.watching_channel.get_with_default(None)
                if watching_channel is not None:
                    new_watching: Channel | None = channels.get(watching_channel.id)
                    if new_watching is not None and self.can_watch(new_watching):
                        self.watch(new_watching, update_status=False)
                    else:
                        self.stop_watching()
                
                self.change_state(State.CHANNEL_SWITCH)

            elif self._state is State.CHANNEL_SWITCH:
                if self.settings.dump:
                    self.gui.close()
                    continue
                logger.info(_("gui", "status", "switching"))
                new_watching = None
                for channel in sorted(channels.values(), key=self.get_priority):
                    if self.can_watch(channel) and self.should_switch(channel):
                        new_watching = channel
                        break
                
                watching_channel = self.watching_channel.get_with_default(None)
                if new_watching is not None:
                    self.watch(new_watching)
                    self._state_change.clear()
                elif watching_channel is not None and self.can_watch(watching_channel):
                    logger.info(_("status", "watching").format(channel=watching_channel.name))
                    self._state_change.clear()
                else:
                    self.print(_("status", "no_channel"))
                    self.change_state(State.IDLE)
            
            elif self._state is State.EXIT:
                logger.info(_("gui", "status", "exiting"))
                break
            await self._state_change.wait()

    async def run(self):
        while True:
            try:
                await self._run()
                break
            except ReloadRequest:
                await self.shutdown()
            except ExitRequest:
                break
            except Exception as exc:
                logger.critical(f"Fatal error encountered: {exc}")
                logger.critical(traceback.format_exc())
                break
        
        logger.info(_("gui", "status", "exiting"))
        await self.shutdown()
