'''
Minimal Marionette client (standard library only).

Marionette is the remote-control protocol built into Firefox - the same
one geckodriver/Selenium use internally. Talking to it directly needs
nothing but a TCP socket and JSON, so no third-party installs.

Usage:
    1. Close all running Firefox windows.
    2. Start Firefox with the protocol enabled:

           firefox -marionette &

    3. Connect from Python:

           client = MarionetteClient()
           client.execute_script("return document.title;")

Protocol (version 3):
    Every message is "<byte length>:<json>". Requests are
    [0, message_id, command_name, params]; responses are
    [1, message_id, error, result].
'''

import json
import socket
import time

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2828  # Firefox's default Marionette port


class MarionetteClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self._buffer = b""
        self._message_id = 0

        # Firefox sends a handshake object on connect, e.g.
        # {"applicationType": "gecko", "marionetteProtocol": 3}
        handshake = self._read_message()
        if handshake.get("marionetteProtocol") != 3:
            raise ConnectionError(
                f"Unsupported Marionette protocol: {handshake}"
            )

        self._command("WebDriver:NewSession", {})

    # ---- wire format -------------------------------------------------
    def _read_message(self):
        """Read one '<length>:<json>' message from the socket."""
        while b":" not in self._buffer:
            self._buffer += self._recv()

        length_bytes, _, rest = self._buffer.partition(b":")
        length = int(length_bytes)

        while len(rest) < length:
            rest += self._recv()

        self._buffer = rest[length:]
        return json.loads(rest[:length])

    def _recv(self):
        data = self.sock.recv(4096)
        if not data:
            raise ConnectionError("Marionette connection closed by Firefox")
        return data

    def _command(self, name, params):
        """Send a command and wait for its matching response."""
        self._message_id += 1
        body = json.dumps([0, self._message_id, name, params]).encode("utf-8")
        self.sock.sendall(str(len(body)).encode("ascii") + b":" + body)

        while True:
            message = self._read_message()
            # Responses are [1, message_id, error, result]
            if (
                isinstance(message, list)
                and message[0] == 1
                and message[1] == self._message_id
            ):
                error, result = message[2], message[3]
                if error:
                    raise RuntimeError(f"Marionette error: {error}")
                return result

    # ---- public API --------------------------------------------------
    def execute_script(self, script):
        """Run JavaScript in the current tab and return its value."""
        result = self._command(
            "WebDriver:ExecuteScript", {"script": script, "args": []}
        )
        return result.get("value") if isinstance(result, dict) else result

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# Example test code
if __name__ == "__main__":
    print("Connecting to Firefox Marionette on port 2828...")
    print("(Start Firefox with:  firefox -marionette)\n")

    client = MarionetteClient()
    print("Connected. Page title:", client.execute_script("return document.title;"))

    start = time.perf_counter()
    client.execute_script("return 0;")
    print(f"JS round-trip latency: {time.perf_counter() - start:.4f}s")

    client.close()
