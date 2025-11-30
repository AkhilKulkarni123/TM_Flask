# api/websocket.py
def init_websocket(sock):

    @sock.route('/ws')
    def websocket_route(ws):
        while True:
            data = ws.receive()
            if data is None:
                break
            ws.send(f"You sent: {data}")
    