import signal
from configparser import ConfigParser
from datetime import timedelta
from threading import Thread

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


def set_cache_time(cache_time=15):
    smartplugs.CACHE_TIME = timedelta(seconds=cache_time)


async def loop():
    global controls, should_run
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
            smartplugs.init(config["GENERAL"]["tapo_user"], config["GENERAL"]["tapo_password"], False)
            await smartplugs.reset_plugs()
    lm.log("Abort - Too many Errors", msg_type=lm.LogType.SystemInfo)
    exit(5)

def console_io_loop():
    global controls
    cmds = {
        "list": {
            "plugs": lambda: "\n".join([str(P)+" connected="+str(int(P.device is not None))+" state="+str(int(asyncio.run(P.is_on()))) for P in smartplugs.plugs.values()]),
            "controllers": lambda: "\n".join([str(C) for C in controls]),
        },
        "exit": lambda: os.kill(os.getpid(), signal.SIGKILL),  # im tired of getting python with asyncio and multiple Threads to close nicely
        "cache": {
            "clear": smartplugs.clear_cache,
            "set": lambda x: set_cache_time(int(x))
        },
        "plug":{
            "read": lambda P: asyncio.run(smartplugs.get_plug(P).is_on()),
            "set": lambda P, S: asyncio.run(smartplugs.get_plug(P).on(S=='1' or S=='True')),
            "toggle": lambda P: asyncio.run(smartplugs.get_plug(P).toggle()),
        },
        "reinit": lambda: smartplugs.init(config["GENERAL"]["tapo_user"], config["GENERAL"]["tapo_password"])
    }
    while True:
        try:
            str_in = input()
            args = str_in.split(" ")
            cmd = cmds
            while type(cmd) == dict:
                if len(args) > 0:
                    option = args.pop(0)
                    try:
                        cmd = cmd[option]
                    except KeyError:
                        lm.log(option, "is not a valid argument\n    Try one of: "+str(cmd.keys()))
                        break
                else:
                    lm.log("Possible options:\n   ", list(cmd.keys()))
                    break
            res = None
            if type(cmd) != dict:
                res = cmd(*args)
                output = str_in
                if res is not None:
                    output += "\n    " + str(res).replace("\n", "\n    ")
                lm.log(output)
        except Exception as e:
            if type(e) == KeyboardInterrupt:
                return
            else:
                lm.log(type(e), "\n", e, msg_type=lm.LogType.Error)


if __name__ == '__main__':
    if os.path.isfile("config.ini"):
        config.read("config.ini")
    controls = []
    set_cache_time(int(config["GENERAL"]["cache_time"]))
    smartplugs.init(config["GENERAL"]["tapo_user"], config["GENERAL"]["tapo_password"])
    for control in [sec for sec in config.sections() if sec.startswith("CONTROLLER_")]:
        con_type = config[control]["type"]
        con = con_types.get(con_type).from_config(config[control])
        controls.append(con)
    lm.log("Starting Software.\n\t\twith", len(smartplugs.plugs), "Plugs, (" + str(len([P for P in smartplugs.plugs.values() if P.virtual])), "Virtual) and", len(controls), "Controllers", msg_type=lm.LogType.SystemInfo)
    Thread(target=console_io_loop, daemon=True).start()
    should_run = True
    asyncio.run(loop())
