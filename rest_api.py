import json

from aiohttp import  web

import smartplugs
import logging_manager as lm


async def post_action(request: web.Request):
    plug = smartplugs.get_plug(request.match_info.get('plug', ""), no_create=True)
    if plug is None:
        return web.Response(status=404)
    await plug.poll_info()
    action = request.match_info.get('action', "")
    if action == "on":
        lm.log("RestAPI set", plug, True, msg_type=lm.LogType.DataUpdated)
        await plug.on(True)
    elif action == "off":
        lm.log("RestAPI set", plug, True, msg_type=lm.LogType.DataUpdated)
        await plug.on(False)
    elif action == "toggle":
        lm.log("RestAPI toggle", plug, msg_type=lm.LogType.DataUpdated)
        await plug.toggle()
    else:
        return web.Response(status=400)
    return web.Response()

async def get_plugs(request: web.Request):
    include_virtual = request.query.get("include_virtual", "0") == "1"
    plugs = [P for P in smartplugs.plugs.values() if include_virtual or not P.virtual]
    output = "ip,name,state\n"
    for P in plugs:
        output += f"\"{P.ip}\",\"{P.name}\",{await P.is_on()}\n"
    return web.Response(text=output, content_type="text/csv")

async def get_plug(request: web.Request):
    plug = smartplugs.get_plug(request.match_info.get('plug', ""), no_create=True)
    if plug is None:
        return web.Response(status=404)
    await plug.poll_info()
    info = {
        "ip": plug.ip,
        "name": plug.name,
        "mac": plug.mac,
        "state": await plug.is_on(),
        "power_draw": await plug.power_draw(),
        "virtual": plug.virtual,
    }
    return web.Response(text=json.dumps(info), content_type="application/json")


def run(host:str = '127.0.0.1', port:int = 8080):
    app = web.Application()
    app.add_routes([
        web.get("/", get_plugs),
        web.get("/{plug}", get_plug),
        web.post("/{plug}/{action}", post_action),
    ])
    web.run_app(app, port=port, host=host)
