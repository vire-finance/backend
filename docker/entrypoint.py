"""Start one ASGI process and pass container termination signals directly to it."""
import os
import sys


def server_command(environ=None):
    environment = os.environ if environ is None else environ
    port = environment.get("PORT", "10000")
    if not isinstance(port, str) or not port.isascii() or not port.isdecimal():
        raise ValueError("PORT must be an integer between 1 and 65535.")
    if not 1 <= int(port) <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535.")
    return [
        sys.executable, "-m", "uvicorn", "app.main:app",
        "--host", "0.0.0.0", "--port", str(int(port)), "--workers", "1",
        "--proxy-headers",
        "--forwarded-allow-ips", environment.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        "--timeout-graceful-shutdown", "25",
    ]


if __name__ == "__main__":
    try:
        command = server_command()
    except ValueError as error:
        sys.exit(str(error))
    os.execv(sys.executable, command)
