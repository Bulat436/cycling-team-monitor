"""Run Uvicorn on loopback with a cooperative Windows stop signal."""
import argparse
import asyncio
import json
import logging
import os
import re
from pathlib import Path

import portalocker
import uvicorn

from .config import Settings
from .main import create_app


async def serve(directory: Path, run_id: str, port: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    marker = directory / "running.json"
    stop = directory / f"{run_id}.stop"
    pending = directory / f"{run_id}.json"
    with portalocker.Lock(directory / "server.lock", timeout=0, fail_when_locked=True):
        marker.unlink(missing_ok=True)
        settings = Settings(serve_frontend=True, expose_api_docs=False)
        server = uvicorn.Server(uvicorn.Config(
            create_app(settings), host="127.0.0.1", port=port, access_log=False,
            proxy_headers=True, forwarded_allow_ips="127.0.0.1", timeout_graceful_shutdown=30,
        ))
        task = asyncio.create_task(server.serve())
        try:
            while not task.done():
                if server.started and not marker.is_file():
                    pending.write_text(json.dumps({"run_id": run_id, "port": port, "pid": os.getpid()}), encoding="utf-8")
                    pending.replace(marker)
                if stop.is_file():
                    server.should_exit = True
                await asyncio.sleep(0.2)
            await task
        finally:
            for path in (marker, stop, pending):
                path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("run_id")
    parser.add_argument("port", type=int)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{32}", args.run_id) or not 1 <= args.port <= 65535:
        parser.error("Invalid run ID or port")
    try:
        asyncio.run(serve(args.directory.resolve(), args.run_id, args.port))
    except portalocker.exceptions.LockException:
        logging.getLogger("uvicorn.error").error("Another instance holds this runtime directory.")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
