from __future__ import annotations

import sys
import signal
import asyncio
import logging
import argparse
import warnings
import traceback
from typing import TYPE_CHECKING

# import an additional thing for proper PyInstaller freeze support
from multiprocessing import freeze_support

import truststore
truststore.inject_into_ssl()

from translate import _
from settings import Settings
from version import __version__
from exceptions import CaptchaRequired
from utils import lock_file
from constants import LOGGING_LEVELS, SELF_PATH, FILE_FORMATTER, LOG_PATH, LOCK_PATH
from tui import TuiLogHandler
from headless import HeadlessTwitch

if TYPE_CHECKING:
    from _typeshed import SupportsWrite

warnings.simplefilter("default", ResourceWarning)

if sys.version_info < (3, 10):
    raise RuntimeError("Python 3.10 or higher is required")

class ParsedArgs(argparse.Namespace):
    _verbose: int
    _debug_ws: bool
    _debug_gql: bool
    log: bool
    dump: bool

    @property
    def logging_level(self) -> int:
        return LOGGING_LEVELS[min(self._verbose, 4)]

    @property
    def debug_ws(self) -> int:
        if self._debug_ws:
            return logging.DEBUG
        elif self._verbose >= 4:
            return logging.INFO
        return logging.NOTSET

    @property
    def debug_gql(self) -> int:
        if self._debug_gql:
            return logging.DEBUG
        elif self._verbose >= 4:
            return logging.INFO
        return logging.NOTSET

def run_headless(settings: Settings):
    logger = logging.getLogger("TwitchDrops")
    logger.setLevel(settings.logging_level)
    if settings.log:
        handler = logging.FileHandler(LOG_PATH)
        handler.setFormatter(FILE_FORMATTER)
        logger.addHandler(handler)

    logging.getLogger("TwitchDrops.gql").setLevel(settings.debug_gql)
    logging.getLogger("TwitchDrops.websocket").setLevel(settings.debug_ws)
    
    # Set language
    try:
        _.set_language(settings.language)
    except ValueError:
        pass  # Stick to English if language not found

    # Run the client
    async def run_client():
        exit_status = 0
        client = HeadlessTwitch(settings)
        tui_handler = TuiLogHandler(client.gui)
        tui_handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(tui_handler)
        loop = asyncio.get_running_loop()

        def signal_handler():
            client.print("Shutdown signal received. Shutting down...")
            if not client.gui.close_requested:
                client.close()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)

        try:
            await client.run()
        except CaptchaRequired:
            exit_status = 1
            client.print(_("error", "captcha"))
        except Exception:
            exit_status = 1
            client.print("Fatal error encountered:\n")
            client.print(traceback.format_exc())
        finally:
            if not client.gui.close_requested:
                await client.shutdown()
            
        client.print(_("status", "terminated"))
        sys.exit(exit_status)

    try:
        # Check if another instance is running
        success, file = lock_file(LOCK_PATH)
        if not success:
            print("Another instance is already running.")
            sys.exit(3)

        asyncio.run(run_client())
    finally:
        if 'file' in locals() and file:
            file.close()

def main():
    freeze_support()
    
    parser = argparse.ArgumentParser(
        SELF_PATH.name,
        description="A program that allows you to mine timed drops on Twitch.",
        allow_abbrev=False
    )

    parser.add_argument("--version", action="version", version=f"v{__version__}")
    parser.add_argument("-v", dest="_verbose", action="count", default=0)
    parser.add_argument("--log", action="store_true")
    parser.add_argument("--dump", action="store_true")
    parser.add_argument(
        "--debug-ws", dest="_debug_ws", action="store_true", help=argparse.SUPPRESS
    )
    parser.add_argument(
        "--debug-gql", dest="_debug_gql", action="store_true", help=argparse.SUPPRESS
    )
    
    args = parser.parse_args(namespace=ParsedArgs())
    
    try:
        settings = Settings(args)
    except Exception:
        print(f"There was an error while loading the settings file:\n\n{traceback.format_exc()}")
        sys.exit(4)
    
    run_headless(settings)

if __name__ == "__main__":
    main()