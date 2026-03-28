# Gemini Code Assistant Context

This document provides context for the Gemini AI assistant to understand the `TwitchDropsMinerV5` project.

## Project Overview

This is `TwitchDropsMinerV5`, a Python application designed to automatically "mine" timed Twitch drops without needing to watch the video stream, thus saving bandwidth. It runs in the background, simulating stream watching by fetching metadata, and automatically handles channel switching and drop claiming.

The application features a Terminal User Interface (TUI) for user interaction, especially for environments like Termux. This TUI allows for managing game priorities and viewing progress within the terminal. Note that the original project might have supported a GUI on other platforms, but for Termux, a TUI is used.

**Key Technologies:**

*   **Language:** Python 3.10+
*   **Core Libraries:**
    *   `aiohttp`: For asynchronous HTTP requests to the Twitch backend.
    *   `rich`: For formatted output in the terminal (likely for logging).
    *   `truststore`: For using the system's SSL certificate store.
*   **Packaging:** `PyInstaller` is used to create standalone executables for Windows and Linux. The `README.md` also mentions `AppImage` for Linux builds.

**Architectural Highlights:**

*   **Asynchronous:** The application is heavily based on `asyncio` to handle network operations and UI updates concurrently.
*   **Modular:** The code is organized into modules with specific responsibilities:
    *   `main.py`: The main entry point, handles argument parsing and application startup.
    *   `headless.py`: Contains the core logic for the "headless" mining process.
    *   `twitch.py`: Manages interactions with the Twitch API.
    *   `settings.py`: Handles loading and saving of application settings.
    *   `tui.py`: Implements the Terminal User Interface (TUI).
    *   `lang/`: Contains JSON files for localization, supporting multiple languages.

## Building and Running

### Setting up the Environment

To set up the development environment, a virtual environment is used.

1.  **Create the virtual environment and install dependencies:**
    ```bash
    ./setup_env.sh
    ```
    This script creates a virtual environment in the `env/` directory and installs the required packages from `requirements.txt`.

### Running from Source

To run the application directly from the source code:

```bash
./run.sh
```

This script executes `python3 main.py` with recommended logging flags (`--log -vvvv`).

### Building the Executable

To build a distributable executable:

```bash
./build.sh
```

This script uses `PyInstaller` to package the application into a single executable file. It requires the environment to be set up first.

## Development Conventions

*   **Virtual Environment:** All development and building are done within a Python virtual environment managed by `setup_env.sh`.
*   **Configuration:** Application settings are handled by the `Settings` class in `settings.py`. Sensitive information like login sessions are stored in `cookies.jar`.
*   **Logging:** The application uses the `logging` module. Verbosity can be controlled with `-v` flags when running `main.py`.
*   **Localization:** User-facing strings are managed through JSON files in the `lang/` directory, facilitated by the `translate.py` module.
*   **Error Handling:** Custom exceptions are defined in `exceptions.py`.
