from typing import Dict

from tapo import ApiClient
import asyncio
import logging_manager as lm
import subprocess
from datetime import datetime, time, timedelta


def ping(host_ip):
    process = subprocess.Popen(['ping', '-W', '1', '-c', '1', host_ip], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    return process.returncode == 0

def stuff_back(s, l=15, c='_'):
    if len(s) < l:
        return s + ''.join([c for _ in range(l-len(s))])
    else:
        return s


class PowerPlug(object):

    def __init__(self, ip):
        self.ip = ip
        self.device = None
        self.id = None
        self.mac = None
        self.name = None
        asyncio.run(self.reset())

    async def poll_info(self):
        if self.device is None:
            if ping(self.ip):
                try:
                    self.device = await client.p110(self.ip)
                except Exception:
                    self.device = None
                    return
            else:
                return
        info = (await self.device.get_device_info()).to_dict()
        self.id = info["device_id"]
        self.mac = info["mac"]
        self.name = info["nickname"]

    async def reset(self):
        self.device = None
        await self.poll_info()
        if self.device is None:
            lm.log("Can not connect to", self.ip, msg_type=lm.LogType.Error)

    async def ensure_connection(self):
        if self.device is None:
            await self.poll_info()
        else:
            await self.device.refresh_session()

    async def on(self, state=True):
        if self.device is None:
            return
        if state:
            await self.device.on()
        else:
            await self.device.off()

    async def is_on(self):
        if self.device is None:
            return False
        info = (await self.device.get_device_info()).to_dict()
        return info["device_on"]

    async def toggle(self):
        if self.device is None:
            return
        await self.on(not (await self.is_on()))

    async def power_draw(self):
        if self.device is None:
            return
        return (await self.device.get_current_power()).to_dict()["current_power"]

    async def delay_on(self, state = True, delay=300):
        if self.device is None:
            return
        await asyncio.sleep(delay)
        await self.on(state)

    def __str__(self):
        return stuff_back(str(self.name))+"@"+self.ip


def init(user, password):
    global client
    client = ApiClient(user, password, 300)


plugs: Dict[str, PowerPlug] = {}

def get_plug(ip):
    if ip in plugs:
        return plugs[ip]
    else:
        P = PowerPlug(ip)
        plugs[ip] = P
        return P


async def reset_plugs():
    global plugs
    await asyncio.gather(*[P.reset() for P in plugs.values()])


async def ensure_connection():
    global plugs
    await asyncio.gather(*[P.ensure_connection() for P in plugs.values()])


class MasterSlave(object):

    def __init__(self, master: PowerPlug, slave: PowerPlug, off_draw: int = 4, on_draw: int = 10, invert: bool = False, update_on: bool = True, update_off: bool = True, force: bool = False):
        self.master = master
        self.slave = slave
        self.off_draw = off_draw
        self.on_draw = on_draw
        self.invert = invert
        self.force = force
        self.update_on = update_on
        self.update_off = update_off
        self.last_update_state = None

    @staticmethod
    def from_config(config):
        return MasterSlave(
            get_plug(config["master"]),
            get_plug(config["slave"]),
            off_draw=int(config["off_draw"]),
            on_draw=int(config["on_draw"]),
            invert=config.get("invert", '0')=='1' or config.get("invert", '0')=='true',
            update_on=config.get("update_on", '1')=='1' or config.get("update_on", '1')=='true',
            update_off=config.get("update_off", '1')=='1' or config.get("update_off", '1')=='true',
            force=config.get("force", '0')=='1' or config.get("force", '0')=='true'
        )

    async def update(self):
        slave_state = self.last_update_state
        master_state = (await self.master.is_on()) ^ self.invert
        if self.force or slave_state is None:
            slave_state = (await self.slave.is_on()) ^ self.invert
        if slave_state:
            if (not master_state) or (await self.master.power_draw() <= self.off_draw):
                self.last_update_state = False ^ self.invert
                if self.update_off:
                    await self.slave.on(False ^ self.invert)
                    lm.log("Set", self.slave, self.last_update_state, msg_type=lm.LogType.DataUpdated)
        else:
            if master_state and (await self.master.power_draw()) >= self.on_draw:
                self.last_update_state = True ^ self.invert
                if self.update_on:
                    await self.slave.on(True ^ self.invert)
                    lm.log("Set", self.slave, self.last_update_state, msg_type=lm.LogType.DataUpdated)


class DevicePingSlave(object):

    def __init__(self, master: str, slave: PowerPlug, invert: bool = False, update_on: bool = True, update_off: bool = True, force: bool = False):
        self.master = master
        self.slave = slave
        self.invert = invert
        self.force = force
        self.update_on = update_on
        self.update_off = update_off
        self.last_update_state = None

    @staticmethod
    def from_config(config):
        return DevicePingSlave(
            config["master"],
            get_plug(config["slave"]),
            invert=config.get("invert", '0')=='1' or config.get("invert", '0')=='true',
            update_on=config.get("update_on", '1')=='1' or config.get("update_on", '1')=='true',
            update_off=config.get("update_off", '1')=='1' or config.get("update_off", '1')=='true',
            force=config.get("force", '0')=='1' or config.get("force", '0')=='true'
        )

    async def update(self):
        slave_state = self.last_update_state
        master_state = ping(self.master) ^ self.invert
        if self.force or slave_state is None:
            slave_state = (await self.slave.is_on()) ^ self.invert
        if slave_state:
            if not master_state:
                self.last_update_state = False ^ self.invert
                if self.update_off:
                    await self.slave.on(False ^ self.invert)
                    lm.log("Set", self.slave, self.last_update_state, msg_type=lm.LogType.DataUpdated)
        else:
            if master_state:
                self.last_update_state = True ^ self.invert
                if self.update_on:
                    await self.slave.on(True ^ self.invert)
                    lm.log("Set", self.slave, self.last_update_state, msg_type=lm.LogType.DataUpdated)


class SimpleTimeControl(object):

    def __init__(self, slave: PowerPlug, ontime: time, offtime: time, update_on: bool = True, update_off: bool = True, force: bool = False):
        self.ontime = ontime
        self.offtime = offtime
        self.slave = slave
        self.force = force
        self.update_on = update_on
        self.update_off = update_off
        self.last_update_state = None

    @staticmethod
    def from_config(config):
        return SimpleTimeControl(
            get_plug(config["slave"]),
            ontime=time.fromisoformat(config["on_time"]),
            offtime=time.fromisoformat(config["off_time"]),
            update_on=config.get("update_on", '1') == '1' or config.get("update_on", '1') == 'true',
            update_off=config.get("update_off", '1') == '1' or config.get("update_off", '1') == 'true',
            force=config.get("force", '0') == '1' or config.get("force", '0') == 'true'
        )

    async def update(self):
        slave_state = self.last_update_state
        now = datetime.now().time()
        off_before_on = self.offtime < self.ontime
        if self.force or slave_state is None:
            slave_state = await self.slave.is_on()
        if slave_state:
            run = False
            if off_before_on:
                run = now >= self.offtime and not now >= self.ontime
            else:
                run = now < self.ontime or now >= self.offtime
            if run:
                self.last_update_state = False
                if self.update_off:
                    await self.slave.on(False)
                    lm.log("Set", self.slave, self.last_update_state, msg_type=lm.LogType.DataUpdated)
        else:
            run = False
            if off_before_on:
                run = now < self.offtime or now >= self.ontime
            else:
                run = now >= self.ontime and now < self.offtime
            if run:
                self.last_update_state = True
                if self.update_on:
                    await self.slave.on(True)
                    lm.log("Set", self.slave, self.last_update_state, msg_type=lm.LogType.DataUpdated)

