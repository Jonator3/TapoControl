from enum import Enum
from datetime import datetime


class LogType(Enum):
    DEFAULT = 0
    Error = 1
    SystemInfo = 2
    DataUpdated = 3
    ConnectionOpen = 4

COLORMAP = {
    LogType.DEFAULT: '\033[0m',
    LogType.Error: '\033[91m',
    LogType.SystemInfo: '\033[95m',
    LogType.ConnectionOpen: '\033[96m',
    LogType.DataUpdated: '\033[94m',
}
MESSAGE_LOG = []

def log(*message, msg_type=LogType.DEFAULT):
    tstamp = datetime.now().strftime("[%Y-%m-%d %H:%M:%S]")
    MESSAGE_LOG.append((tstamp, " ".join([str(m) for m in message]), msg_type))
    message = [tstamp] + list(message)
    msg = " ".join([str(m) for m in message])
    if msg_type != LogType.DEFAULT:
        msg = COLORMAP[msg_type] + msg + COLORMAP[LogType.DEFAULT]
    print(msg)
