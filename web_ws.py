#!/usr/bin/env python3
"""Example for aiohttp.web websocket server
"""

import asyncio
import aiohttp
import os
import json
import re
import magic
import pickle
from datetime import datetime
#from urllib.parse import quote
from cgi import escape as quote
from aiohttp.web import (Application, Response,
                         WebSocketResponse, WSClientDisconnectedError)

WS_FILE = os.path.join(os.path.dirname(__file__), 'index.html')

URL_M = re.compile('http[s]?://(?:[a-zA-Z]|[0-9]|[~$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')
YOUTUBE_URL_M = re.compile('(https?://)?(www\.)?'
                           '(youtube|youtu|youtube-nocookie)\.(com|be)/'
                           '(watch\?v=|embed/|v/|.+\?v=)?([^&=%\?]{11})(.*)')

COUB_URL_M = re.compile('(https?://)?(www\.)?(coub\.com/view)/(.*)')
INSTAGRAM_URL_M = re.compile('(https?://)?(www\.)?'
                        '(http://instagram\.com/p/)([^&=%\?]{10})')
FACEBOOK_URL_M = re.compile('(https?://)?(www\.)?(facebook\.com)/(.+)')

@asyncio.coroutine
def try_url(request, url):
    try:
        matched = YOUTUBE_URL_M.match(url)
        if matched:
            url = '//www.youtube.com/embed/{}{}'.format(matched.groups()[-2],
                                                        matched.groups()[-1])
            return url, b'youtube'

        matched = COUB_URL_M.match(url)

        if matched:
            url = '//coub.com/embed/{}'.format(matched.groups()[-1])
            return url, b'coub'

        matched = INSTAGRAM_URL_M.match(url)
        if matched:
            url = 'http://instagram.com/p/{}/media/?size=l'.format(matched.groups()[-1])
            return url, b'image'
        
        matched = FACEBOOK_URL_M.match(url)
        if matched:
            return url, b'facebook'

        print('trying url {} ...'.format(url))
        req = yield from aiohttp.request('get', url)

        stream = req.content
        buf = yield from stream.read(100)

        req.close()
        mime = magic.from_buffer(buf, mime=True)
        return url, mime
    except Exception as err:
        print( err )
        return url, b'unknown'


@asyncio.coroutine
def parse_message(request, msg):
    resp = ''
    idx = 0
    for matched in URL_M.finditer(msg):
        o_url = msg[matched.start() : matched.end()]
        url, m_type = yield from try_url(request, o_url)

        resp += quote(msg[idx : idx+matched.start()]).replace('\n', '<br/>') 
        if m_type.startswith(b'image'):
            resp += '<div class="small_image"><img src="{}" class="img-rounded" height="200" onclick="show_image(\'{}\', \'{}\')"/></div>'.format(url, o_url, url)
        elif m_type in (b'youtube', b'coub', b'instagram'):
            resp += '<div class="youtube_video"><div class="embed-responsive embed-responsive-16by9">'\
              '<iframe class="embed-responsive-item" src="{}" allowfullscreen></iframe>'\
              '</div></div>'.format(url)
        elif m_type == b'facebook':
            resp += '<div class="fb-post" data-href="{}" data-width="500"></div>'.format(url)
        else:
            resp += '<a href="{}" target="_blank">{}</a>'.format(url, url)

        idx = matched.end()
    resp += quote(msg[idx:]).replace('\n', '<br/>')
    return resp



def ws_send(ws, time, user, message):
    ws.send_str(json.dumps({'user': user, 'message': message, 'time': time}))

@asyncio.coroutine
def wshandler(request):
    time = datetime.now().strftime('%H:%M:%S')
    resp = WebSocketResponse()
    ok, protocol = resp.can_start(request)
    if not ok:
        with open(WS_FILE, 'rb') as fp:
            return Response(body=fp.read(), content_type='text/html')

    resp.start(request)

    user = yield from resp.receive_str()
    print('%s joined.'%user)

    if user in request.app['users']:
        ws_send(resp, time, 'SERVER',
                'User "{}" is already exists in the chat.'.format(user))
        raise WSClientDisconnectedError()

    request.app['users'].append(user)

    for ws in request.app['sockets']:
        ws_send(ws, time, 'SERVER', '{} joined'.format(user))

    request.app['sockets'].append(resp)

    try:
        while True:
            msg = yield from resp.receive_str()
            msg = yield from parse_message(request, msg)

            print(msg)

            for ws in request.app['sockets']:
                ws_send(ws, time, user, msg)

            request.app['history'].push((time, user, msg))
    except WSClientDisconnectedError:
        if resp not in request.app['sockets']:
            return resp
        request.app['sockets'].remove(resp)
        request.app['users'].remove(user)
        print('%s disconnected.'%user)
        for ws in request.app['sockets']:
            ws_send(ws, time, 'SERVER', '{} disconnected'.format(user))
        raise

@asyncio.coroutine
def get_history(request):
    cnt = int(request.match_info['cnt'])
    idx = int(request.match_info['idx'])

    hist = request.app['history'].slice(cnt, idx)
    hist.reverse()

    return Response(text=json.dumps(hist),  content_type='application/json')


class History:
    def __init__(self, hist_len=100):
        self.__hist = []
        self.__len = hist_len

    def push(self, item):
        self.__hist.insert(0, item)
        if len(self.__hist) > self.__len:
            self.__hist.pop()

    def slice(self, cnt, idx=0):
        return self.__hist[idx : idx+cnt]

    def dump(self, fpath):
        dump = pickle.dumps(self.__hist)
        open(fpath, 'bw').write(dump)
        print('history saved in {} ...'.format(fpath))

    def load(self, fpath):
        if not os.path.exists(fpath):
            print('dump file {} does not found ...'.format(fpath))
            return
        dump = open(fpath, 'rb').read()
        self.__hist = pickle.loads(dump)



@asyncio.coroutine
def init(loop):
    app = Application(loop=loop)
    app['sockets'] = []
    app['users'] = []
    app['history'] = History()
    app['history'].load('history.dump')
    app.router.add_route('GET', '/', wshandler)
    app.router.add_route('GET', '/get_history/{cnt}/{idx}', get_history)

    handler = app.make_handler()
    srv = yield from loop.create_server(handler, '0.0.0.0', 8080)
    print("Server started at http://127.0.0.1:8080")
    return app, srv, handler


@asyncio.coroutine
def finish(app, srv, handler):
    for ws in app['sockets']:
        ws.close()
    app['sockets'].clear()
    app['history'].dump('history.dump')
    yield from asyncio.sleep(0.1)
    srv.close()
    yield from handler.finish_connections()
    yield from srv.wait_closed()


loop = asyncio.get_event_loop()
app, srv, handler = loop.run_until_complete(init(loop))
try:
    loop.run_forever()
except KeyboardInterrupt:
    loop.run_until_complete(finish(app, srv, handler))
except Exception:
    loop.run_until_complete(finish(app, srv, handler))
