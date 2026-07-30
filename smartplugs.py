from collections.abc import Callable
from typing import Dict, List, Iterable

from tapo import ApiClient
import asyncio
import logging_manager as lm
import subprocess
import os
from datetime import datetime, time, timedelta
import re

regex_ipv4 = re.compile("[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+")
EPOCH = datetime(year=1970, month=1, day=1)
CACHE_TIME = timedelta(seconds=15)
ping_cache = {}


def ping(host_ip):
    ct, cv = ping_cache.get(host_ip, (EPOCH, False))
    if ct + CACHE_TIME > datetime.now():
        return cv
    else:
        process = subprocess.Popen(['ping', '-W', '3', '-c', '3', host_ip], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate()
        ping_cache[host_ip] = (datetime.now(), process.returncode == 0)
        return process.returncode == 0

def stuff_back(s, l=15, c='_'):
    if len(s) < l:
        return s + ''.join([c for _ in range(l-len(s))])
    else:
        return s


class PowerPlug(object):

    def __init__(self, ip):
        if regex_ipv4.match(ip):
            self.ip = ip
            self.id = None
            self.name = None
            self.virtual = False
        else:
            self.ip = None
            self.id = "VIRTUAL_PLUG_" + ip
            self.name = ip
            self.virtual = True
            self.state = False
        self.mac = None
        self.device = None
        if not self.virtual:
            self.cache = {
                  "is_on": (EPOCH, False),
                  "power_draw": (EPOCH, -1),
            }
        if asyncio.get_event_loop_policy()._local._loop is None:
            asyncio.run(self.reset())

    async def poll_info(self):
        if self.virtual:
            return
        if self.device is None:
            try:
                self.device = await client.p110(self.ip)
            except Exception as e:
                lm.log("Plug connection error:\n\t", type(e), "\n", e, msg_type=lm.LogType.Error)
                self.device = None
                return
        info = (await self.device.get_device_info()).to_dict()
        self.id = info["device_id"]
        self.mac = info["mac"]
        self.name = info["nickname"]

    async def reset(self):
        if self.virtual:
            return
        self.device = None
        await self.poll_info()
        if self.device is None:
            lm.log("Can not connect to", self.ip, msg_type=lm.LogType.Error)

    async def ensure_connection(self):
        if self.virtual:
            return
        if self.device is None:
            await self.poll_info()
            if self.device is not None:
                lm.log("Connected to", self, msg_type=lm.LogType.ConnectionOpen)
        else:
            await self.device.refresh_session()

    async def on(self, state=True):
        if self.virtual:
            self.state = state
        if self.device is None:
            return
        self.cache["is_on"] = (datetime.now(), state)
        if state:
            await self.device.on()
        else:
            await self.device.off()

    async def is_on(self):
        if self.virtual:
            return self.state
        elif self.cache["is_on"][0] + CACHE_TIME < datetime.now():
            if self.device is None:
                return False
            info = (await self.device.get_device_info()).to_dict()
            self.cache["is_on"] = (datetime.now(), info["device_on"])
        return self.cache["is_on"][1]

    async def toggle(self):
        if self.virtual:
            self.state = not self.state
        if self.device is None:
            return
        await self.on(not (await self.is_on()))

    async def power_draw(self):
        if self.virtual:
            return -1
        elif self.cache["power_draw"][0] + CACHE_TIME < datetime.now():
            if self.device is None:
                self.cache["power_draw"] = (datetime.now(), -1)
            self.cache["power_draw"] = (datetime.now(), (await self.device.get_current_power()).to_dict()["current_power"])
        return self.cache["power_draw"][1]

    def clear_cache(self):
        self.cache = {
            "is_on": (EPOCH, False),
            "power_draw": (EPOCH, -1),
        }

    def __str__(self):
        ip = self.ip
        if self.virtual:
            ip = "VIRTUAL_PLUG "
        return stuff_back(str(self.name))+"@"+ip


plugs: Dict[str, PowerPlug] = {}
plug_aliases: Dict[str, str] = {}

def add_plug_alias(reverence: str, alias: str):
    global plug_aliases
    plug_aliases[alias] = reverence

def get_plug(reverence:str, *, no_create:bool = False) -> PowerPlug | None:
    global plugs
    if reverence == "":
        return None
    if reverence.startswith(":"):
        return get_plug(plug_aliases.get(reverence[1:], ""), no_create=no_create)
    elif reverence.startswith("#"):
        for P in plugs.values():
            if P.name == reverence[1:]:
                return P
        return None
    if reverence in plugs:
        return plugs[reverence]
    elif not no_create:
        P = PowerPlug(reverence)
        plugs[reverence] = P
        return P
    else:
        return None

async def reset_plugs():
    global plugs
    await asyncio.gather(*[P.reset() for P in plugs.values()])

async def ensure_connection():
    global plugs
    await asyncio.gather(*[P.ensure_connection() for P in plugs.values()])


def init(user, password, auto_reset_plugs=True):
    global client
    client = ApiClient(user, password, 300)
    if auto_reset_plugs:
        asyncio.run(reset_plugs())

def clear_cache():
    global ping_cache, plugs
    ping_cache.clear()
    for P in plugs.values():
        P.clear_cache()


class MasterSlave(object):

    def __init__(self, masters: List[PowerPlug], slaves: List[PowerPlug], *, off_draw: int = 4, on_draw: int = 10, trigger:Callable[[Iterable[object]], bool] = any, invert: bool = False, update_on: bool = True, update_off: bool = True, force: bool = False):
        self.masters = masters
        self.slaves = slaves
        self.off_draw = off_draw
        self.on_draw = on_draw
        self.invert = invert
        self.trigger = trigger
        self.force = force
        self.update_on = update_on
        self.update_off = update_off
        self.last_update_state = None

    @staticmethod
    def from_config(config):
        return MasterSlave(
            [get_plug(master) for master in config["master"].split(';')],
            [get_plug(slave) for slave in config["slave"].split(';')],
            off_draw=int(config["off_draw"]),
            on_draw=int(config["on_draw"]),
            trigger={"any": any, "all": all}.get(config.get("trigger", None), any),
            invert=config.get("invert", '0')=='1' or config.get("invert", '0')=='true',
            update_on=config.get("update_on", '1')=='1' or config.get("update_on", '1')=='true',
            update_off=config.get("update_off", '1')=='1' or config.get("update_off", '1')=='true',
            force=config.get("force", '0')=='1' or config.get("force", '0')=='true'
        )

    async def update(self):
        update_state = None
        for slave in self.slaves:
            slave_state = self.last_update_state
            master_state = self.trigger([(await master.is_on()) for master in self.masters]) ^ self.invert
            if self.force or slave_state is None:
                slave_state = (await slave.is_on()) ^ self.invert
            if slave_state:
                if (not master_state) or self.trigger([(await master.power_draw() <= self.off_draw) for master in self.masters]):
                    update_state = False ^ self.invert
                    if self.update_off:
                        await slave.on(False ^ self.invert)
                        lm.log("Set", slave, update_state, msg_type=lm.LogType.DataUpdated)
            else:
                if master_state and self.trigger([(await master.power_draw()) >= self.on_draw for master in self.masters]):
                    update_state = True ^ self.invert
                    if self.update_on:
                        await slave.on(True ^ self.invert)
                        lm.log("Set", slave, update_state, msg_type=lm.LogType.DataUpdated)
        if update_state is not None:
            self.last_update_state = update_state

    def __str__(self):
        out = "MasterSlave"
        out += "\n      master Plugs:\n        "+"\n        ".join([str(P)+" connected="+str(P.device is not None) for P in self.masters])
        out += "\n      slave  Plugs:\n        "+"\n        ".join([str(P)+" connected="+str(P.device is not None) for P in self.slaves])
        return out


class SyncMasterSlave(object):

    def __init__(self, masters: List[PowerPlug], slaves: List[PowerPlug], *, trigger:Callable[[Iterable[object]], bool] = any, invert: bool = False, update_on: bool = True, update_off: bool = True, force: bool = False):
        self.masters = masters
        self.slaves = slaves
        self.invert = invert
        self.trigger = trigger
        self.force = force
        self.update_on = update_on
        self.update_off = update_off
        self.last_update_state = None

    @staticmethod
    def from_config(config):
        return SyncMasterSlave(
            [get_plug(master) for master in config["master"].split(';')],
            [get_plug(slave) for slave in config["slave"].split(';')],
            trigger={"any": any, "all": all}.get(config.get("trigger", None), any),
            invert=config.get("invert", '0')=='1' or config.get("invert", '0')=='true',
            update_on=config.get("update_on", '1')=='1' or config.get("update_on", '1')=='true',
            update_off=config.get("update_off", '1')=='1' or config.get("update_off", '1')=='true',
            force=config.get("force", '0')=='1' or config.get("force", '0')=='true'
        )

    async def update(self):
        update_state = None
        for slave in self.slaves:
            slave_state = self.last_update_state
            master_state = self.trigger([(await master.is_on()) for master in self.masters]) ^ self.invert
            if self.force or slave_state is None:
                slave_state = (await slave.is_on())
            if slave_state:
                if (not master_state):
                    update_state = False
                    if self.update_off:
                        await slave.on(False)
                        lm.log("Set", slave, update_state, msg_type=lm.LogType.DataUpdated)
            else:
                if master_state:
                    update_state = True
                    if self.update_on:
                        await slave.on(True)
                        lm.log("Set", slave, update_state, msg_type=lm.LogType.DataUpdated)
        if update_state is not None:
            self.last_update_state = update_state

    def __str__(self):
        out = "SyncSlave"
        out += "\n      master Plugs:\n        "+"\n        ".join([str(P)+" connected="+str(P.device is not None) for P in self.masters])
        out += "\n      slave  Plugs:\n        "+"\n        ".join([str(P)+" connected="+str(P.device is not None) for P in self.slaves])
        return out


class DelayController(object):

    def __init__(self, masters: List[PowerPlug], slaves: List[PowerPlug], *, delay:int = 30, trigger:Callable[[Iterable[object]], bool] = any, invert: bool = False, cancel_changes:bool = False, update_on: bool = True, update_off: bool = True, force: bool = False):
        self.masters = masters
        self.slaves = slaves
        self.delay = delay
        self.invert = invert
        self.trigger = trigger
        self.force = force
        self.cancel_changes = cancel_changes
        self.update_on = update_on
        self.update_off = update_off
        self.last_update_state = None
        self.current_state = None
        self.mem = []

    @staticmethod
    def from_config(config):
        return DelayController(
            [get_plug(master) for master in config["master"].split(';')],
            [get_plug(slave) for slave in config["slave"].split(';')],
            delay=int(config["delay"]),
            trigger={"any": any, "all": all}.get(config.get("trigger", None), any),
            invert=config.get("invert", '0')=='1' or config.get("invert", '0')=='true',
            cancel_changes=config.get("cancel_changes", '1')=='1' or config.get("cancel_changes", '1')=='true',
            update_on=config.get("update_on", '1')=='1' or config.get("update_on", '1')=='true',
            update_off=config.get("update_off", '1')=='1' or config.get("update_off", '1')=='true',
            force=config.get("force", '0')=='1' or config.get("force", '0')=='true'
        )

    async def update(self):
        update_state = None
        now = datetime.now()
        master_state = self.trigger([(await master.is_on()) for master in self.masters]) ^ self.invert
        if self.last_update_state is None:
            self.last_update_state = not master_state
        if self.last_update_state:
            if (not master_state):
                update_state = False
                if self.update_off:
                    self.mem.append((now + timedelta(minutes=self.delay), False))
                    if self.cancel_changes:
                        self.mem = [(t, v) for t, v in self.mem if v==False]
        else:
            if master_state:
                update_state = True
                if self.update_on:
                    self.mem.append((now + timedelta(minutes=self.delay), True))
                    if self.cancel_changes:
                        self.mem = [(t, v) for t, v in self.mem if v==True]
        if update_state is not None:
            self.last_update_state = update_state
        while len(self.mem) > 0:
            time, state = self.mem[0]
            if time <= now:
                self.current_state = state
                del self.mem[0]
                for slave in self.slaves:
                    await slave.on(state)
                    lm.log("Set", slave, state, msg_type=lm.LogType.DataUpdated)
            else:
                break
        if self.force and (self.current_state is not None):
            for slave in self.slaves:
                if slave.is_on() != self.current_state:
                    await slave.on(self.current_state)
                    lm.log("Set", slave, self.current_state, msg_type=lm.LogType.DataUpdated)

    def __str__(self):
        out = "Delay"
        out += "\n      master Plugs:\n        "+"\n        ".join([str(P)+" connected="+str(P.device is not None) for P in self.masters])
        out += "\n      slave  Plugs:\n        "+"\n        ".join([str(P)+" connected="+str(P.device is not None) for P in self.slaves])
        return out


class DevicePingSlave(object):

    def __init__(self, masters: List[str], slaves: List[PowerPlug], *, trigger:Callable[[Iterable[object]], bool] = any, invert: bool = False, update_on: bool = True, update_off: bool = True, force: bool = False):
        if any([not regex_ipv4.match(master) for master in masters]):
            raise ValueError("Masters must be Valid IPv4: "+str(masters))
        self.masters = masters
        self.slaves = slaves
        self.trigger = trigger
        self.invert = invert
        self.force = force
        self.update_on = update_on
        self.update_off = update_off
        self.last_update_state = None

    @staticmethod
    def from_config(config):
        return DevicePingSlave(
            config["master"].split(';'),
            [get_plug(slave) for slave in config["slave"].split(';')],
            trigger={"any": any, "all": all}.get(config.get("trigger", None), any),
            invert=config.get("invert", '0')=='1' or config.get("invert", '0')=='true',
            update_on=config.get("update_on", '1')=='1' or config.get("update_on", '1')=='true',
            update_off=config.get("update_off", '1')=='1' or config.get("update_off", '1')=='true',
            force=config.get("force", '0')=='1' or config.get("force", '0')=='true'
        )

    async def update(self):
        update_state = None
        for slave in self.slaves:
            slave_state = self.last_update_state
            master_state = self.trigger([ping(master) for master in self.masters]) ^ self.invert
            if self.force or slave_state is None:
                slave_state = (await slave.is_on()) ^ self.invert
            if slave_state:
                if not master_state:
                    update_state = False ^ self.invert
                    if self.update_off:
                        await slave.on(False ^ self.invert)
                        lm.log("Set", slave, update_state, msg_type=lm.LogType.DataUpdated)
            else:
                if master_state:
                    update_state = True ^ self.invert
                    if self.update_on:
                        await slave.on(True ^ self.invert)
                        lm.log("Set", slave, update_state, msg_type=lm.LogType.DataUpdated)
        if update_state is not None:
            self.last_update_state = update_state

    def __str__(self):
        out = "DevicePingSlave"
        out += "\n      master Plugs:\n        "+"\n        ".join([IP for IP in self.masters])
        out += "\n      slave  Plugs:\n        "+"\n        ".join([str(P)+" connected="+str(P.device is not None) for P in self.slaves])
        return out


class SimpleTimeControl(object):

    def __init__(self, slaves: List[PowerPlug], ontime: time, offtime: time, update_on: bool = True, update_off: bool = True, force: bool = False):
        self.ontime = ontime
        self.offtime = offtime
        self.slaves = slaves
        self.force = force
        self.update_on = update_on
        self.update_off = update_off
        self.last_update_state = None

    @staticmethod
    def from_config(config):
        return SimpleTimeControl(
            [get_plug(slave) for slave in config["slave"].split(';')],
            ontime=time.fromisoformat(config["on_time"]),
            offtime=time.fromisoformat(config["off_time"]),
            update_on=config.get("update_on", '1') == '1' or config.get("update_on", '1') == 'true',
            update_off=config.get("update_off", '1') == '1' or config.get("update_off", '1') == 'true',
            force=config.get("force", '0') == '1' or config.get("force", '0') == 'true'
        )

    async def update(self):
        update_state = None
        for slave in self.slaves:
            slave_state = self.last_update_state
            now = datetime.now().time()
            off_before_on = self.offtime < self.ontime
            if self.force or slave_state is None:
                slave_state = await slave.is_on()
            if slave_state:
                run = False
                if off_before_on:
                    run = now >= self.offtime and not now >= self.ontime
                else:
                    run = now < self.ontime or now >= self.offtime
                if run:
                    update_state = False
                    if self.update_off:
                        await slave.on(False)
                        lm.log("Set", slave, update_state, msg_type=lm.LogType.DataUpdated)
            else:
                run = False
                if off_before_on:
                    run = now < self.offtime or now >= self.ontime
                else:
                    run = now >= self.ontime and now < self.offtime
                if run:
                    update_state = True
                    if self.update_on:
                        await slave.on(True)
                        lm.log("Set", slave, update_state, msg_type=lm.LogType.DataUpdated)
        if update_state is not None:
            self.last_update_state = update_state

    def __str__(self):
        out = "SimpleTime"
        out += "\n      master Plugs:\n        "+"\n        ".join([str(P)+" connected="+str(P.device is not None) for P in self.masters])
        out += "\n      slave  Plugs:\n        "+"\n        ".join([str(P)+" connected="+str(P.device is not None) for P in self.slaves])
        return out

