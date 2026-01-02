from __future__ import annotations
import asyncio
from collections import deque
import logging
from threading import Thread
from typing import TYPE_CHECKING
from rich.console import Group
from rich.layout import Layout
from rich.panel import Panel
from rich.progress import Progress, BarColumn, TextColumn
from rich.table import Table
from rich.live import Live
from rich.text import Text
from rich.align import Align
from datetime import datetime
from prompt_toolkit.input import create_input
from prompt_toolkit.keys import Keys
from constants import State

if TYPE_CHECKING:
    from headless import HeadlessTwitch

logger = logging.getLogger("TwitchDrops")

class TuiManager:
    def __init__(self, twitch: "HeadlessTwitch"):
        # Core App State
        self.twitch = twitch
        self.close_requested = False
        self._thread = Thread(target=self.run, daemon=True)
        self.refresh_event = asyncio.Event()

        # View/Mode Management
        self.view_mode = 'dashboard'  # dashboard, campaigns
        self.sub_view_mode = None # set_priority
        
        # Input Handling
        self.input = None
        
        # UI State
        self.log_messages = deque(maxlen=20)
        self.auth_info = None
        
        # Campaigns View State
        self.campaign_selection_cursor = 0
        self.scroll_offset = 0
        self.campaign_panel_height = 15
        
        # Priority Input State
        self.priority_input = ""
        self.campaign_for_priority = None
        
        # Layouts
        self.dashboard_layout = self.make_dashboard_layout()
        self.campaign_layout = self.make_campaign_layout()

        # Mock objects for compatibility
        self.login = self.MockLogin(self)
        self.tray = self.MockTray()
        self.status = self.MockStatus()
        self.channels = self.MockChannels()
        self.progress = self.MockProgress()
        self.inv = self.MockInventory()
        self.websockets = self.MockWebsocketStatus()

    # --- Core TUI Lifecycle ---

    def start(self):
        self._thread.start()

    def run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self.main())
        finally:
            loop.close()

    async def main(self):
        self.input = create_input()
        loop = asyncio.get_running_loop()

        with self.input.raw_mode():
            loop.add_reader(self.input.fileno(), self.on_input)
            try:
                with Live(self.dashboard_layout, screen=True, redirect_stderr=False, refresh_per_second=10) as live:
                    while not self.close_requested:
                        if self.view_mode == 'dashboard':
                            layout = self.dashboard_layout
                            self.update_dashboard_layout(layout)
                        elif self.view_mode == 'campaigns':
                            layout = self.campaign_layout
                            self.update_campaign_layout(layout)
                        
                        live.update(layout)
                        
                        try:
                            await asyncio.wait_for(self.refresh_event.wait(), timeout=1)
                            self.refresh_event.clear()
                        except asyncio.TimeoutError:
                            pass
            finally:
                loop.remove_reader(self.input.fileno())

    # --- Input Handling ---

    def on_input(self):
        for key_press in self.input.read_keys():
            key = key_press.key
            if isinstance(key, Keys):
                key = key.value

            # Explicitly route key presses based on current view/sub-view
            if self.sub_view_mode == 'set_priority':
                self.handle_priority_input(key)
            elif self.view_mode == 'campaigns':
                self.handle_campaigns_input(key)
            elif self.view_mode == 'dashboard':
                self.handle_dashboard_input(key)

            self.refresh_event.set()

    def handle_dashboard_input(self, key: str):
        if key in ('q', 'c-c'):
            self.close()
        elif key == 'c':
            self.show_campaigns_view()

    def handle_campaigns_input(self, key: str):
        if key in ('q', 'd', 'c-c'):
            self.show_dashboard_view()
        elif key == 'up':
            self.cursor_up()
        elif key == 'down':
            self.cursor_down()
        elif key == ' ':
            self.toggle_campaign_selection()
        elif key == 'x':
            self.deselect_all_campaigns()

    def handle_priority_input(self, key: str):
        if key.isnumeric():
            self.priority_input += key
        elif key in ('backspace', 'c-h'):
            self.priority_input = self.priority_input[:-1]
        elif key in ('enter', 'c-j', 'c-m'):
            self.confirm_priority()
        elif key in ('escape', 'c-['):
            self.cancel_set_priority()

    # --- View/Mode Switching ---

    def show_dashboard_view(self):
        self.view_mode = 'dashboard'
        if self.sub_view_mode is None: # Only save if exiting campaign view, not cancelling priority
            self.twitch.settings.save()
            self.twitch.restart_watching()
        self.sub_view_mode = None

    def show_campaigns_view(self):
        self.view_mode = 'campaigns'
        self.sub_view_mode = None
        self.campaign_selection_cursor = 0
        self.scroll_offset = 0

    def close(self):
        if not self.close_requested:
            self.close_requested = True
            self.twitch.change_state(State.EXIT)

    # --- Campaign & Priority Logic ---

    def cursor_up(self):
        self.campaign_selection_cursor = max(0, self.campaign_selection_cursor - 1)
        if self.campaign_selection_cursor < self.scroll_offset:
            self.scroll_offset = self.campaign_selection_cursor

    def cursor_down(self):
        if self.twitch.inventory:
            inventory_size = len(self.twitch.inventory)
            self.campaign_selection_cursor = min(inventory_size - 1, self.campaign_selection_cursor + 1)
            if self.campaign_selection_cursor >= self.scroll_offset + self.campaign_panel_height:
                self.scroll_offset = self.campaign_selection_cursor - self.campaign_panel_height + 1
    
    def toggle_campaign_selection(self):
        if self.twitch.inventory and len(self.twitch.inventory) > self.campaign_selection_cursor:
            campaign = self.twitch.inventory[self.campaign_selection_cursor]
            game_name = campaign.game.name
            if game_name in self.twitch.settings.priority:
                self.twitch.settings.priority.remove(game_name)
            else:
                self.twitch.settings.priority.append(game_name)
    
    def enter_set_priority_mode(self):
        if self.twitch.inventory and len(self.twitch.inventory) > self.campaign_selection_cursor:
            self.campaign_for_priority = self.twitch.inventory[self.campaign_selection_cursor]
            game_name = self.campaign_for_priority.game.name
            if game_name in self.twitch.settings.priority:
                self.priority_input = str(self.twitch.settings.priority.index(game_name) + 1)
            else:
                self.priority_input = ""
            self.sub_view_mode = 'set_priority'

    def confirm_priority(self):
        try:
            if self.priority_input:
                new_prio = int(self.priority_input)
                game_name = self.campaign_for_priority.game.name
                
                if game_name in self.twitch.settings.priority:
                    self.twitch.settings.priority.remove(game_name)
                
                self.twitch.settings.priority.insert(new_prio - 1, game_name)
                self.twitch.settings.save()
        except (ValueError, IndexError):
            pass
        finally:
            self.cancel_set_priority()

    def deselect_all_campaigns(self):
        self.twitch.settings.priority = []
        self.twitch.settings.save()

    # --- Layout Definitions ---

    def make_dashboard_layout(self) -> Layout:
        layout = Layout()
        layout.split(
            Layout(self.get_header("Dashboard"), name="header", size=3),
            Layout(ratio=1, name="main"),
            Layout(name="footer", size=4)
        )
        layout["main"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=2)
        )
        layout["left"].split(
            Layout(name="prio_list"), 
            Layout(name="status")
        )
        layout["right"].split(
            Layout(name="progress"), 
            Layout(name="logs")
        )
        return layout

    def make_campaign_layout(self) -> Layout:
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="campaigns")
        )
        return layout

    # --- UI Panel & Component Builders ---
    
    def get_header(self, title: str) -> Panel:
        grid = Table.grid(expand=True)
        grid.add_column(justify="left", ratio=1)
        grid.add_column(justify="center", ratio=1)
        grid.add_column(justify="right", ratio=1)
        grid.add_row(
            "[b]Twitch Drops Miner[/b]",
            f"[bold green]{title}[/bold green]",
            datetime.now().ctime(),
        )
        return Panel(grid, style="bold magenta")

    # --- Dashboard View Panels ---

    def update_dashboard_layout(self, layout: Layout):
        layout["header"].update(self.get_header("Dashboard"))
        layout["footer"].update(self.get_dashboard_footer())
        layout["prio_list"].update(self.get_prio_list_panel())
        layout["status"].update(self.get_status_panel())
        layout["progress"].update(self.get_progress_panel())
        layout["logs"].update(self.get_logs_panel())

    def get_dashboard_footer(self) -> Panel:
        return Panel(Text("[C]ampaigns    [Q]uit", justify="center"), style="bold blue")

    def get_prio_list_panel(self) -> Panel:
        priority_list = self.twitch.settings.priority
        games_table = Table(expand=True, show_header=False)
        games_table.add_column("Prio", style="green", width=3)
        games_table.add_column("Farming Game", style="cyan")
        
        if priority_list:
            for i, game_name in enumerate(priority_list):
                games_table.add_row(f"{i+1}.", game_name)
        else:
            games_table.add_row("", "No games prioritized.")
        return Panel(games_table, title="[bold green]Farming Priority[/bold green]", border_style="green")

    def get_status_panel(self) -> Panel:
        watching_channel = self.twitch.watching_channel.get_with_default(None)
        
        renderable = None
        if self.auth_info:
            content = (
                f"Go to: [bold cyan]{self.auth_info['uri']}[/bold cyan]\n"
                f"Enter code: [bold magenta]{self.auth_info['code']}[/bold magenta]"
            )
            renderable = Text.from_markup(content)  # Left-aligned by default
        else:
            content = ""
            if watching_channel and watching_channel.online:
                content += f"[b]Watching:[/b] [cyan]{watching_channel.name}[/cyan]\n"
                content += f"[b]Game:[/b] [magenta]{watching_channel.game.name if watching_channel.game else 'N/A'}[/magenta]"
            else:
                content = "Not watching any channel."
            renderable = Align.center(Text.from_markup(content), vertical="middle")

        return Panel(renderable, title="[bold blue]Status[/bold blue]", border_style="blue")
    
    def get_progress_panel(self) -> Panel:
        active_campaign = self.twitch.get_active_campaign()
        if not active_campaign:
            return Panel(Align.center("No active drop.", vertical="middle"), title="[bold cyan]Drop Progress[/bold cyan]", border_style="cyan")
        
        drop = active_campaign.first_drop
        if not drop or drop.is_claimed:
            return Panel(Align.center("No active drop.", vertical="middle"), title="[bold cyan]Drop Progress[/bold cyan]", border_style="cyan")
        
        progress = Progress(
            TextColumn(drop.name, style="bold blue"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.completed}/{task.total} min"),
        )
        progress.add_task("drop", total=drop.required_minutes, completed=drop.current_minutes)
        return Panel(progress, title="[bold cyan]Drop Progress[/bold cyan]", border_style="cyan")

    def get_logs_panel(self) -> Panel:
        log_renderable = Group(*[Text(msg) for msg in self.log_messages])
        return Panel(log_renderable, title="[bold red]Logs[/bold red]", border_style="red")

    # --- Campaigns View Panels ---

    def update_campaign_layout(self, layout: Layout):
        layout["header"].update(self.get_campaign_selection_header())
        if self.sub_view_mode == 'set_priority':
            layout["campaigns"].update(self.get_priority_input_panel())
        else:
            layout["campaigns"].update(self.get_campaign_selection_panel())
    
    def get_campaign_selection_header(self) -> Panel:
        return Panel(Text("[D]ashboard    [X] Deselect All    [P]riority    [Space]Select    [Q]uit", justify="center"), style="bold blue")

    def get_campaign_selection_panel(self) -> Panel:
        campaigns_table = Table(expand=True, show_header=False)
        campaigns_table.add_column("Prio", style="green", width=6)
        campaigns_table.add_column("L", style="white", width=2)
        campaigns_table.add_column("Campaign", style="cyan")
        campaigns_table.add_column("Ends At", style="yellow", width=16)
        
        if self.twitch.inventory:
            visible_campaigns = self.twitch.inventory[self.scroll_offset:self.scroll_offset + self.campaign_panel_height]
            for i, campaign in enumerate(visible_campaigns, start=self.scroll_offset):
                game_name = campaign.game.name
                priority_str = ""
                try:
                    priority = self.twitch.settings.priority.index(game_name) + 1
                    priority_str = f"  [b]{priority}[/b]"
                except ValueError:
                    priority_str = "  [dim]•[/dim]"
                
                cursor = ">" if i == self.campaign_selection_cursor else " "
                ends_at = campaign.ends_at.strftime("%Y-%m-%d %H:%M") if campaign.ends_at else "N/A"
                linked_str = "🔗" if campaign.linked else " "
                campaigns_table.add_row(f"{cursor}{priority_str}", linked_str, campaign.game.name, ends_at)
        else:
            campaigns_table.add_row("", "", "No campaigns available.", "")
            
        return Panel(campaigns_table, title="[bold blue]Campaign Management[/bold blue]", border_style="blue")
    
    def get_priority_input_panel(self) -> Panel:
        game_name = self.campaign_for_priority.game.name if self.campaign_for_priority else ""
        text = Text(f"Set priority for '{game_name}': {self.priority_input}", justify="center")
        return Panel(Align.center(text, vertical="middle"), title="[bold yellow]Set Priority (Enter to confirm, Esc to cancel)[/bold yellow]", border_style="yellow")

    # --- Compatibility Mock objects ---
    def print(self, message: str):
        self.log_messages.append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
    def prevent_close(self):
        pass
    def save(self, force: bool = False):
        self.twitch.settings.save(force=force)
    def stop(self):
        self.close()
    def close_window(self):
        pass
    def grab_attention(self, sound=False):
        pass
    async def wait_until_closed(self):
        while not self.close_requested:
            await asyncio.sleep(0.1)
    async def coro_unless_closed(self, coro):
        return await coro
    def set_games(self, games):
        pass
    def display_drop(self, *args, **kwargs):
        pass
    def clear_drop(self, *args, **kwargs):
        pass
    class MockWebsocketStatus:
        def update(self, *args, **kwargs):
            pass
        def remove(self, *args, **kwargs):
            pass
    class MockLogin:
        def __init__(self, tui_manager: "TuiManager"):
            self.tui_manager = tui_manager
        def update(self, *args, **kwargs):
            pass
        async def ask_login(self, *args, **kwargs):
            from exceptions import CaptchaRequired
            logger.error("Interactive login not supported in headless mode.")
            raise CaptchaRequired()
        async def ask_enter_code(self, verification_uri, user_code):
            self.tui_manager.auth_info = {"uri": verification_uri, "code": user_code}
        def clear(self, *args, **kwargs):
            self.tui_manager.auth_info = None
    class MockTray:
        def change_icon(self, icon: str):
            pass
        def notify(self, *args, **kwargs):
            pass
    class MockStatus:
        def update(self, message: str):
            logger.info(message)
    class MockChannels:
        def clear(self):
            pass
        def get_selection(self):
            return None
        def set_watching(self, channel):
            pass
        def clear_watching(self):
            pass
        def display(self, *args, **kwargs):
            pass
    class MockProgress:
        def stop_timer(self):
            pass
        def minute_almost_done(self):
            return False
    class MockInventory:
        def clear(self):
            pass
        async def add_campaign(self, campaign):
            pass
        def update_drop(self, *args, **kwargs):
            pass
