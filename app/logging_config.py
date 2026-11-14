import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging() -> None:
    """Log to the console and to logs/trader.log (rotated at 5 MB, 3 backups).

    Level comes from LOG_LEVEL (default INFO). The Anthropic SDK and HTTP client loggers stay at
    WARNING even in DEBUG mode: their debug output includes request bodies, which carry the
    TradingView access token passed to the MCP connector.
    """
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_DIR.mkdir(exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file = RotatingFileHandler(LOG_DIR / "trader.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    file.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [console, file]
    root.setLevel(level)
    for name in ("anthropic", "httpx2", "httpcore2", "mcp"):
        logging.getLogger(name).setLevel(logging.WARNING)
