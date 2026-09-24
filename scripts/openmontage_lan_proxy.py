from __future__ import annotations

import asyncio
import signal
import sys


BUFFER_SIZE = 1024 * 128


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _handle_client(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    target_host: str,
    target_port: int,
) -> None:
    try:
        target_reader, target_writer = await asyncio.open_connection(target_host, target_port)
    except Exception:
        client_writer.close()
        try:
            await client_writer.wait_closed()
        except Exception:
            pass
        return

    upstream = asyncio.create_task(_pipe(client_reader, target_writer))
    downstream = asyncio.create_task(_pipe(target_reader, client_writer))
    done, pending = await asyncio.wait(
        {upstream, downstream}, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    await asyncio.gather(*done, return_exceptions=True)


async def main() -> None:
    if len(sys.argv) != 5:
        raise SystemExit(
            "usage: openmontage_lan_proxy.py <listen-host> <listen-port> <target-host> <target-port>"
        )

    listen_host = sys.argv[1]
    listen_port = int(sys.argv[2])
    target_host = sys.argv[3]
    target_port = int(sys.argv[4])

    server = await asyncio.start_server(
        lambda reader, writer: _handle_client(reader, writer, target_host, target_port),
        listen_host,
        listen_port,
        reuse_address=True,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    sockets = ", ".join(str(sock.getsockname()) for sock in (server.sockets or []))
    print(
        f"VideoGen OpenMontage LAN proxy listening on {sockets} -> {target_host}:{target_port}",
        flush=True,
    )

    async with server:
        await stop_event.wait()


if __name__ == "__main__":
    asyncio.run(main())
