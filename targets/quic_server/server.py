"""
QUIC / HTTP/3 Target Server
-----------------------------
Minimal aioquic HTTP/3 server. Accepts requests and returns simple responses.
For POST requests, reads the full request body (the gen-quic generator's
configurable payload) and reports how many bytes were received, mirroring
the HTTP/2 target server's /data endpoint.
Uses a self-signed TLS certificate generated in the Dockerfile.
"""

import asyncio, threading
from aioquic.asyncio.server import serve
from aioquic.h3.connection import H3_ALPN
from aioquic.h3.events import HeadersReceived, DataReceived
from aioquic.quic.configuration import QuicConfiguration
from aioquic.asyncio.protocol import QuicConnectionProtocol
from aioquic.h3.connection import H3Connection

HOST = "0.0.0.0"
PORT = 4433

counter = {"requests": 0, "bytes_in": 0}
_lock   = threading.Lock()


class H3Handler(QuicConnectionProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._h3: H3Connection | None = None
        # Per-stream state while a request is still being received.
        self._methods: dict[int, bytes] = {}
        self._bodies: dict[int, bytearray] = {}

    def quic_event_received(self, event):
        if self._h3 is None:
            self._h3 = H3Connection(self._quic, enable_webtransport=False)

        for h3_event in self._h3.handle_event(event):
            if isinstance(h3_event, HeadersReceived):
                method = b"GET"
                for name, value in h3_event.headers:
                    if name == b":method":
                        method = value
                        break
                with _lock:
                    counter["requests"] += 1

                if h3_event.stream_ended:
                    self._respond(h3_event.stream_id, method, b"")
                else:
                    self._methods[h3_event.stream_id] = method
                    self._bodies[h3_event.stream_id] = bytearray()

            elif isinstance(h3_event, DataReceived):
                buf = self._bodies.get(h3_event.stream_id)
                if buf is not None:
                    buf.extend(h3_event.data)
                if h3_event.stream_ended:
                    method = self._methods.pop(h3_event.stream_id, b"GET")
                    body = bytes(self._bodies.pop(h3_event.stream_id, b""))
                    self._respond(h3_event.stream_id, method, body)

    def _respond(self, stream_id, method: bytes, body: bytes):
        if method == b"POST":
            with _lock:
                counter["bytes_in"] += len(body)
            payload = ('{"received":%d}' % len(body)).encode()
        else:
            payload = b'{"status":"ok","server":"QUIC target"}'

        self._h3.send_headers(
            stream_id=stream_id,
            headers=[
                (b":status", b"200"),
                (b"content-type", b"application/json"),
            ],
        )
        self._h3.send_data(stream_id=stream_id, data=payload, end_stream=True)
        self.transmit()


async def main():
    config = QuicConfiguration(
        alpn_protocols=H3_ALPN,
        is_client=False,
    )
    config.load_cert_chain("cert.pem", "key.pem")

    await serve(HOST, PORT, configuration=config, create_protocol=H3Handler)
    await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())