import logging
from os import environ
from pathlib import Path
from typing import List, Optional

from dodal.log import LOGGER as dodal_logger
from dodal.log import set_up_logging_handlers as setup_dodal_logging

LOGGER = logging.getLogger("Hyperion")
LOGGER.setLevel(logging.DEBUG)
LOGGER.parent = dodal_logger

ISPYB_LOGGER = logging.getLogger("Hyperion ISPyB and Zocalo callbacks")
ISPYB_LOGGER.setLevel(logging.DEBUG)

NEXUS_LOGGER = logging.getLogger("Hyperion NeXus callbacks")
NEXUS_LOGGER.setLevel(logging.DEBUG)

ALL_LOGGERS = [LOGGER, ISPYB_LOGGER, NEXUS_LOGGER]


class DCGIDFilter(logging.Filter):
    dc_group_id: Optional[str] = None

    def filter(self, record):
        if self.dc_group_id:
            record.dc_group_id = self.dc_group_id
        return True


dc_group_id_filter = DCGIDFilter()


def set_dcgid_tag(dcgid):
    """Set the datacollection group id as a tag on all subsequent log messages.
    Setting to None will remove the tag."""
    dc_group_id_filter.dc_group_id = dcgid


def set_up_logging_handlers(
    logger=dodal_logger,
    logging_level: str | None = "INFO",
    dev_mode: bool = False,
    filename="hyperion.txt",
) -> List[logging.Handler]:
    """Set up the logging level and instances for user chosen level of logging.

    Mode defaults to production and can be switched to dev with the --dev flag on run.
    """
    handlers = setup_dodal_logging(
        logging_level,
        dev_mode,
        _get_logging_file_path(filename),
        file_handler_logging_level="DEBUG",
        logger=logger,
    )
    dodal_logger.addFilter(dc_group_id_filter)
    logger.addFilter(dc_group_id_filter)

    return handlers


def _get_logging_file_path(filename: str) -> Path:
    """Get the path to write the hyperion log files to.

    If the HYPERION_LOG_DIR environment variable exists then logs will be put in here.
    If no environment variable is found it will default it to the ./tmp/dev directory.
    If the directories needed don't exist they will be created.

    Returns:
        logging_path (Path): Path to the log file for the file handler to write to.
    """
    logging_path = Path(environ.get("HYPERION_LOG_DIR") or "./tmp/dev/")
    Path(logging_path).mkdir(parents=True, exist_ok=True)
    return logging_path / Path(filename)
