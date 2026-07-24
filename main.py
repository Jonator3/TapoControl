from configparser import ConfigParser
from datetime import timedelta

import smartplugs
from smartplugs import MasterSlave, DevicePingSlave, SimpleTimeControl, SyncMasterSlave, DelayController
import asyncio
import os
import logging_manager as lm


con_types = {
    "MasterSlave": MasterSlave,
    "ms": MasterSlave,
    "SimpleTimeControl": SimpleTimeControl,
    "stc": SimpleTimeControl,
    "tc": SimpleTimeControl,
    "DevicePingSlave": DevicePingSlave,
    "dps": DevicePingSlave,
    "dp": DevicePingSlave,
    "SyncMasterSlave": SyncMasterSlave,
    "SyncSlave": SyncMasterSlave,
    "ss": SyncMasterSlave,
    "DelayController": DelayController,
    "dc": DelayController,
}
controls = []
config = ConfigParser()
config["General"] = {"poll_time": "5", "cache_time": "15", "enable_rest_api": "0"}
config["REST_API"] = {"host": "127.0.0.1", "port": "8080"}


async def loop():
    global controls
    try_counter = 0
    poll_time = float(config["GENERAL"]["poll_time"])
    while try_counter < 5:
        try:
            try_counter += 1
            await smartplugs.ensure_connection()
            await asyncio.gather(*[C.update() for C in controls])
            await asyncio.sleep(poll_time)
            try_counter = 0
        except Exception as e:
            lm.log(str(e), msg_type=lm.LogType.Error)
            smartplugs.init(config["GENERAL"]["tapo_user"], config["GENERAL"]["tapo_password"])
            await smartplugs.reset_plugs()
    lm.log("Abort - Too many Errors", msg_type=lm.LogType.SystemInfo)
    exit(5)


if __name__ == '__main__':
    if os.path.isfile("config.ini"):
        config.read("config.ini")
    controls = []
    smartplugs.CACHE_TIME = timedelta(seconds=int(config["GENERAL"]["cache_time"]))
    smartplugs.init(config["GENERAL"]["tapo_user"], config["GENERAL"]["tapo_password"])
    for control in [sec for sec in config.sections() if sec.startswith("CONTROLLER_")]:
        con_type = config[control]["type"]
        con = con_types.get(con_type).from_config(config[control])
        controls.append(con)
    lm.log("Starting Software.\n\t\twith", len(smartplugs.plugs), "Plugs, (" + str(len([P for P in smartplugs.plugs.values() if P.virtual])), "Virtual) and", len(controls), "Controllers", msg_type=lm.LogType.SystemInfo)
    asyncio.run(loop())
