#
# Copyright (C) 2021 Stephane "Twidi" Angel <s.angel@twidi.com>
#
# This file is part of StreamDeckFS
# (see https://github.com/twidi/streamdeckfs).
#
# License: MIT, see https://opensource.org/licenses/MIT
#
import asyncio
import base64
import hashlib
import json
import logging
import threading
from contextvars import ContextVar
from functools import partial
from hmac import compare_digest
from uuid import uuid4

import aiohttp_jinja2
import jinja2
from aiohttp import WSMsgType, web

from .common import ASSETS_PATH, SERIAL_RE_PART, Manager, logger

CLIENTS = ContextVar("CLIENTS", default={})
DECKS = ContextVar("DECKS", default={})
WATCHED_SERIALS = ContextVar("WATCHED_SERIALS", default={})

AUTH_COOKIE = "AUTH"
AUTH_PATH = "/auth"


def get_auth_token(app, password):
    key = b"StreamDeckFS " + app["uri"].encode("utf-8")
    auth_token = hashlib.blake2b(key=key, digest_size=16)
    auth_token.update((password or "").encode("utf-8"))
    return auth_token.hexdigest()


def create_app_runner(host, port, is_ssl, password, from_web_queue):
    app = web.Application()
    app["uri"] = f"http{'s' if is_ssl else ''}://{host}:{port}"
    app["from_web_queue"] = from_web_queue

    if password:
        app["auth_token"] = get_auth_token(app, password)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    aiohttp_jinja2.setup(app, loader=jinja2.PackageLoader("streamdeckfs", "assets/web/templates"))

    app.add_routes(
        [
            web.get("/", page_index),
            web.get(AUTH_PATH, page_auth),
            web.post(AUTH_PATH, page_auth),
            web.get(r"/{serial:" + SERIAL_RE_PART + "}", page_deck),
            web.static("/statics", ASSETS_PATH / "web/statics"),
        ]
    )
    app.add_routes([])

    return web.AppRunner(app)


def run_webserver(host, port, ssl_context, password, loop, from_web_queue, to_web_queue):
    asyncio.set_event_loop(loop)

    runner = create_app_runner(host, port, ssl_context is not None, password, from_web_queue)

    to_web_queue.start(loop)
    from_web_queue.start(loop)

    loop.run_until_complete(runner.setup())

    server = web.TCPSite(runner, host, port, ssl_context=ssl_context)

    server_task = loop.create_task(server.start())
    to_web_queue_task = loop.create_task(handle_to_web_queue(to_web_queue, loop, server_task))

    threading.Thread(name="WebQueue", target=handle_from_web_queue, args=(from_web_queue,)).start()

    loop.run_until_complete(asyncio.wait([server_task, to_web_queue_task]))

    loop.close()


async def on_startup(app):
    logger.info(
        f"[WEB] Web server is running at {app['uri']}{' (password protected)' if app.get('auth_token') else ''}"
    )


async def on_shutdown(app):
    clients = CLIENTS.get()
    for client in clients.values():
        await clients["websocket"].close()
    clients.clear()


def register_client(websocket):
    client_id = str(uuid4())
    clients = CLIENTS.get()
    clients[client_id] = {"id": client_id, "websocket": websocket, "serial": None}
    logger.debug(f'[WEB] Registered new client "{client_id}"')
    return clients[client_id]


async def unregister_client(client):
    clients = CLIENTS.get()
    websocket = client["websocket"]
    client_id = client["id"]
    if serial := client["serial"]:
        watched_serials = WATCHED_SERIALS.get()
        watched_serials.setdefault(serial, set()).discard(client_id)
    del clients[client_id]
    try:
        await websocket.close()
    except Exception:
        pass
    logger.debug(f'[WEB] Unregistered client "{client_id}"')
    return websocket


async def get_client(request):
    ws_current = web.WebSocketResponse()
    ws_ready = ws_current.can_prepare(request)
    if not ws_ready.ok:
        return None
    await ws_current.prepare(request)
    client = register_client(ws_current)
    await ws_current.send_json({"event": "ws.ready", "client_id": client["id"]})
    return client


async def authenticate(request, next):
    if not (auth_token := request.app.get("auth_token")):
        return
    if not compare_digest(request.cookies.get(AUTH_COOKIE, ""), auth_token):
        logger.debug("[WEB] Invalid auth token received via http")
        response = web.HTTPFound(AUTH_PATH + f"?next={next}")
        response.del_cookie(AUTH_COOKIE)
        raise response


async def page_index(request):
    client = await get_client(request)
    if client is None:
        await authenticate(request, "/")
        logger.debug('[WEB] Render page "index"')
        return aiohttp_jinja2.render_template(
            "index.jinja2",
            request,
            {
                "decks": DECKS.get(),
                "auth_token": request.app.get("auth_token"),
            },
        )

    try:
        await handle_websockets_messages(request, client)
    finally:
        return await unregister_client(client)


def validate_next(data):
    next = data.get("next")
    if next and not next.startswith("/") and next.count("/") != 1:
        next = None
    return next


async def page_auth(request):
    if not (auth_token := request.app.get("auth_token")):
        return web.HTTPFound("/")

    if request.method == "POST":
        data = await request.post()

        next = validate_next(data)

        if compare_digest(get_auth_token(request.app, data.get("password")), auth_token):
            logger.debug("[WEB] Valid password received")
            response = web.HTTPFound(next or "/")
            response.set_cookie(AUTH_COOKIE, auth_token)
            return response

        logger.debug("[WEB] Invalid password received")
        response = web.HTTPFound(AUTH_PATH + (f"?next={next}" if next else ""))
        response.del_cookie(AUTH_COOKIE)
        return response

    next = validate_next(request.rel_url.query)
    logger.debug('[WEB] Render page "auth"')
    return aiohttp_jinja2.render_template("auth.jinja2", request, {"next": next})


async def handle_websockets_messages(request, client):
    watched_serials = WATCHED_SERIALS.get()
    websocket = client["websocket"]
    auth_token = request.app.get("auth_token")

    while True:
        msg = await websocket.receive()
        if msg.type != WSMsgType.text:
            break
        try:
            data = json.loads(msg.data)

            if auth_token and not compare_digest(data.get("token", ""), auth_token):
                logger.debug("[WEB] Invalid auth token received via websocket")
                serial = data.get("serial") or client.get("serial")
                await send_to_clients(
                    {
                        "event": "ws.fail",
                        "client_id": client["id"],
                        "auth_url": "/auth?next=" + (f"/{serial}" if serial else "/"),
                    }
                )
                break

            if data["event"] == "web.ready":
                if serial := data.get("serial"):  # filled if on serial page, else it's index page
                    client["serial"] = serial
                    watched_serials.setdefault(serial, set()).add(client["id"])
                await request.app["from_web_queue"].async_put(
                    {
                        "event": data["event"],
                        "serial": serial,
                        "client_id": client["id"],
                    }
                )
                continue

            if data["event"] in ("web.key.pressed", "web.key.released"):
                await request.app["from_web_queue"].async_put(
                    {
                        "event": data["event"],
                        "serial": data["serial"],
                        "key": tuple(data["key"]),
                    }
                )
                continue

        except Exception:
            if logger.level <= logging.DEBUG:
                logger.error(f"[WEB] Cannot handle received websocket message `{msg}`", exc_info=True)


async def page_deck(request):
    serial = request.match_info["serial"]

    await authenticate(request, f"/{serial}")

    decks = DECKS.get()
    if not (deck := decks.get(serial, {})).get("connected"):
        return web.HTTPFound("/")

    logger.debug(f'[WEB] Render page "deck" ({serial=})')
    return aiohttp_jinja2.render_template(
        "deck.jinja2",
        request,
        {
            "deck": deck,
            "auth_token": request.app.get("auth_token"),
        },
    )


async def send_to_clients(data, serial=None):
    clients = CLIENTS.get()

    if client_id := data.get("client_id"):
        if (client := clients.get(client_id)) is None:
            return
        clients = {client_id: client}

    elif serial:
        watched_serials = WATCHED_SERIALS.get()
        clients = {client_id: clients[client_id] for client_id in watched_serials.get(serial, set())}

    for client in clients.values():
        try:
            await client["websocket"].send_json(data)
        except Exception:
            if logger.level <= logging.DEBUG:
                logger.error(
                    f'[WEB] Cannot send websocket message `{data}` to client "{client["id"]}"', exc_info=True
                )


async def handle_to_web_queue(queue, loop, server_task):
    watched_serials = WATCHED_SERIALS.get()
    while True:
        try:
            item = await queue.async_get()
        except Exception:
            break
        if not item:
            # an empty item is a request to end
            queue.async_task_done()
            break
        try:

            if item["event"] == "deck.started":
                decks = DECKS.get()
                decks[item["serial"]] = item["deck"] | {"connected": True}
                await send_to_clients(item)
                continue

            if item["event"] == "deck.stopped":
                decks = DECKS.get()
                if item["serial"] in decks:
                    decks[item["serial"]]["connected"] = False
                await send_to_clients(item)
                continue

            if item["event"] in ("deck.key.pressed", "deck.key.released"):
                if watched_serials.get(item["serial"]):
                    await send_to_clients(item, serial=item["serial"])
                continue

            if item["event"] == "deck.key.updated":
                if watched_serials.get(item["serial"]):
                    if item["image"] is not None:
                        item["image"] = base64.b64encode(item["image"]).decode("ascii")
                    await send_to_clients(item, serial=item["serial"])
                continue

        except Exception:
            if logger.level <= logging.DEBUG:
                logger.error(f"[WEB] Cannot handle message received in the sync->async queue `{item}`", exc_info=True)
        queue.async_task_done()
    server_task.cancel()


def start_web_thread(host, port, ssl_context, password):
    loop = asyncio.new_event_loop()
    to_web_queue = SharedQueue()
    from_web_queue = SharedQueue()
    web_thread = threading.Thread(
        name="WebServer",
        target=run_webserver,
        args=(host, port, ssl_context, password, loop, from_web_queue, to_web_queue),
    )
    web_thread.start()
    return to_web_queue, from_web_queue, partial(stop_web_server, to_web_queue, from_web_queue)


def stop_web_server(to_web_queue, from_web_queue):
    from_web_queue.sync_put(None)
    to_web_queue.sync_put(None)


def handle_from_web_queue(queue):
    decks = DECKS.get()

    while True:
        try:
            item = queue.sync_get()
        except Exception:
            break
        if not item:
            # an empty item is a request to end
            queue.async_task_done()
            break
        try:

            if item["event"] == "web.ready":
                client_id = item["client_id"]
                if serial := item.get("serial"):
                    if not decks.get(serial, {}).get("connected"):
                        Manager.on_deck_stopped(serial, client_id)
                        continue
                    else:
                        Manager.on_deack_ready(serial, client_id)
                Manager.on_web_ready(client_id, serial)
                continue

            if item["event"] == "web.key.pressed":
                Manager.on_web_key_pressed(item["serial"], item["key"])
                continue

            if item["event"] == "web.key.released":
                Manager.on_web_key_released(item["serial"], item["key"])
                continue

        except Exception:
            if logger.level <= logging.DEBUG:
                logger.error(f"[WEB] Cannot handle message received in the async->sync queue `{item}`", exc_info=True)
        queue.async_task_done()


class SharedQueue:
    # based on https://stackoverflow.com/a/59650685

    def __init__(self):
        self._loop = None
        self._queue = None
        self.started = False

    def start(self, loop):
        self._loop = loop
        self._queue = asyncio.Queue()
        self.started = True

    def can_put(self, item):
        if self._loop.is_closed():
            self.started = False
            return False
        if item is None:
            if self.started:
                self.started = False
                return True
            return False
        else:
            return self.started

    def sync_put(self, item):
        if not self.can_put(item):
            return
        asyncio.run_coroutine_threadsafe(self._queue.put(item), self._loop).result()

    def sync_get(self):
        return asyncio.run_coroutine_threadsafe(self._queue.get(), self._loop).result()

    def sync_join(self):
        asyncio.run_coroutine_threadsafe(self._queue.join(), self._loop).result()

    async def async_put(self, item):
        if not self.can_put(item):
            return
        await self._queue.put(item)

    async def async_get(self):
        return await self._queue.get()

    def async_task_join(self):
        self._queue.task_join()

    def async_task_done(self):
        self._queue.task_done()
