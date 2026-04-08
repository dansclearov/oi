"""Constants for LLM CLI application."""

# Chat constants
MIN_MESSAGES_FOR_SMART_TITLE = 8
MAX_TITLE_LENGTH = 75

# UI Navigation
DEFAULT_PAGE_SIZE = 10
INITIAL_PAGE = 0
INITIAL_SELECTED_INDEX = 0

# Renderer Settings
USE_STYLED_RENDERER = True  # Set to False to use legacy PlainTextRenderer

# Model Configuration
DEFAULT_FALLBACK_MODEL = "sonnet"

# Interaction Keys
NAVIGATION_KEYS = {
    "UP": ["\x1b[A", "\x10", "k"],  # Up arrow, Ctrl+P, k
    "DOWN": ["\x1b[B", "\x0e", "j"],  # Down arrow, Ctrl+N, j
    "ENTER": ["\r", "\n"],  # Enter
    "NEXT_PAGE": ["n", "\x0c"],  # n, Ctrl+L
    "PREV_PAGE": ["p", "\x08"],  # p, Ctrl+H
    "BOOKMARK": "b",  # Toggle bookmark
    "FILTER_BOOKMARKED": "f",  # Filter bookmarked chats
    "DELETE": "d",  # First d for delete (dd to confirm)
    "QUIT": ["q", "\x03"],  # q, Ctrl+C
}
