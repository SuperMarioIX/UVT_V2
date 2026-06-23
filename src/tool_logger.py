import logging
import os
from pathlib import Path

class SizeAwareFileHandler(logging.FileHandler):
    """
    File handler that, once the file exceeds soft_limit_mb, only writes
    WARNING and ERROR (and CRITICAL) messages. DEBUG/INFO are dropped.
    """

    def __init__(
        self,
        filename: str,
        mode: str = "a",
        encoding: str = "utf-8",
        delay: bool = False,
        soft_limit_mb: int = 50,
    ) -> None:
        self.soft_limit_bytes = soft_limit_mb * 1024 * 1024
        super().__init__(filename, mode=mode, encoding=encoding, delay=delay)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            # Ensure file is open
            if self.stream is None:
                self.stream = self._open()

            # Check current size
            try:
                # If possible, use underlying file descriptor position
                self.stream.flush()
                current_size = self.stream.tell()
            except Exception:
                # Fallback to os.path.getsize
                try:
                    current_size = os.path.getsize(self.baseFilename)
                except Exception:
                    current_size = 0

            # If over limit and log level < WARNING, drop the record
            if current_size >= self.soft_limit_bytes and record.levelno < logging.WARNING:
                return

        except Exception:
            # On any problem during size check, fall back to normal emit
            pass

        super().emit(record)


class HardLimitFileHandler(logging.FileHandler):
    """
    File handler that completely stops writing once the file reaches
    hard_limit_mb. No DEBUG/INFO/WARN/ERROR will be written after that.
    """

    def __init__(
        self,
        filename: str,
        mode: str = "a",
        encoding: str = "utf-8",
        delay: bool = False,
        hard_limit_mb: int = 500,
    ):
        super().__init__(filename, mode=mode, encoding=encoding, delay=delay)
        self.hard_limit_bytes = hard_limit_mb * 1024 * 1024
        self._limit_reached = False

    def emit(self, record):
        if self._limit_reached:
            return  # refuse all writes once limit reached

        try:
            if self.stream is None:
                self.stream = self._open()

            self.stream.flush()
            current_size = self.stream.tell()
        except Exception:
            try:
                current_size = os.path.getsize(self.baseFilename)
            except:
                current_size = 0

        if current_size >= self.hard_limit_bytes:
            self._limit_reached = True
            return  # completely stop logging

        super().emit(record)

def setup_logger() -> logging.Logger:
    logger = logging.getLogger("WhipserCorelog")
    if logger.handlers:
        return logger  # already configured

    logger.setLevel(logging.DEBUG)

    log_path = Path("debug.log")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setLevel(logging.WARNING)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh.setFormatter(fmt)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.debug("Logger initialized, output to %s", log_path.resolve())
    return logger

logger = setup_logger()