from __future__ import annotations

from typing import Any, TypedDict, TYPE_CHECKING

from yarl import URL

from utils import json_load, json_save
from constants import SETTINGS_PATH, DEFAULT_LANG, PriorityMode

if TYPE_CHECKING:
    from main import ParsedArgs


class SettingsFile(TypedDict):
    auth_token: str
    proxy: URL
    language: str
    dark_mode: bool
    exclude: set[str]
    priority: list[str]
    connection_quality: int
    ntfy_topic: str
    ntfy_server: str
    ntfy_enabled: bool
    enable_badges_emotes: bool
    available_drops_check: bool
    priority_mode: PriorityMode
    ui_colors: dict[str, str]  # Custom UI colors mapping


default_settings: SettingsFile = {
    "auth_token": "",
    "proxy": URL(),
    "priority": [],
    "exclude": set(),
    "dark_mode": False,
    "connection_quality": 1,
    "language": DEFAULT_LANG,
    "ntfy_topic": "",
    "ntfy_server": "https://ntfy.sh",
    "ntfy_enabled": False,
    "enable_badges_emotes": False,
    "available_drops_check": False,
    "priority_mode": PriorityMode.PRIORITY_ONLY,
    "ui_colors": {
        "header": "bold magenta",
        "footer": "bold blue",
        "priority_panel": "green",
        "status_panel": "blue",
        "progress_panel": "cyan",
        "logs_panel": "red",
        "mascot_panel": "yellow",
        "priority_number": "green",
        "farming_game": "cyan",
        "status_text": "magenta",
        "log_text": "default",
        "mascot_text": "pink",
        "scroll_indicator": "dim",
        "status_active": "bold green",
        "status_inactive": "bold red"
    },
}


class Settings:
    # from args
    log: bool
    tray: bool
    dump: bool
    # args properties
    debug_ws: int
    debug_gql: int
    logging_level: int
    # from settings file
    auth_token: str
    proxy: URL
    language: str
    dark_mode: bool
    exclude: set[str]
    priority: list[str]
    connection_quality: int
    ntfy_topic: str
    ntfy_server: str
    ntfy_enabled: bool
    enable_badges_emotes: bool
    available_drops_check: bool
    priority_mode: PriorityMode
    ui_colors: dict[str, str]

    PASSTHROUGH = ("_settings", "_args", "_altered")

    def __init__(self, args: ParsedArgs):
        self._settings: SettingsFile = json_load(SETTINGS_PATH, default_settings)
        self._args: ParsedArgs = args
        self._altered: bool = False

    # default logic of reading settings is to check args first, then the settings file
    def __getattr__(self, name: str, /) -> Any:
        if name in self.PASSTHROUGH:
            # passthrough
            return getattr(super(), name)
        elif hasattr(self._args, name):
            return getattr(self._args, name)
        elif name in self._settings:
            return self._settings[name]  # type: ignore[literal-required]
        return getattr(super(), name)

    def __setattr__(self, name: str, value: Any, /) -> None:
        if name in self.PASSTHROUGH:
            # passthrough
            return super().__setattr__(name, value)
        elif name in self._settings:
            self._settings[name] = value  # type: ignore[literal-required]
            self._altered = True
            return
        raise TypeError(f"{name} is missing a custom setter")

    def __delattr__(self, name: str, /) -> None:
        raise RuntimeError("settings can't be deleted")

    def alter(self) -> None:
        self._altered = True

    def save(self, *, force: bool = False) -> None:
        if self._altered or force:
            json_save(SETTINGS_PATH, self._settings, sort=True)
