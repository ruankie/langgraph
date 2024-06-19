import asyncio
import os
import signal
import sys
from contextlib import contextmanager
from typing import Callable, Optional, cast

import click.exceptions


@contextmanager
def Runner():
    if hasattr(asyncio, "Runner"):
        with asyncio.Runner() as runner:
            yield runner
    else:

        class _Runner:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def run(self, coro):
                asyncio.run(coro)

        yield _Runner()


async def subp_exec(
    cmd: str,
    *args: str,
    input: Optional[str] = None,
    wait: Optional[float] = None,
    verbose: bool = False,
    collect: bool = False,
    on_stdout: Optional[Callable[[str], Optional[bool]]] = None,
) -> tuple[Optional[str], Optional[str]]:
    if verbose:
        cmd_str = f"+ {cmd} {' '.join(map(str, args))}"
        if input:
            print(cmd_str, " <\n", "\n".join(filter(None, input.splitlines())), sep="")
        else:
            print(cmd_str)
    if wait:
        await asyncio.sleep(wait)

    try:
        proc = await asyncio.create_subprocess_exec(
            cmd,
            *args,
            stdin=asyncio.subprocess.PIPE if input else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        def signal_handler():
            # make sure process exists, then terminate it
            if proc.returncode is None:
                proc.terminate()

        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGINT, signal_handler)
        loop.add_signal_handler(signal.SIGTERM, signal_handler)

        empty_fut: asyncio.Future = asyncio.Future()
        empty_fut.set_result(None)
        stdout, stderr, _ = await asyncio.gather(
            monitor_stream(
                cast(asyncio.StreamReader, proc.stdout),
                collect=True,
                display=verbose,
                on_line=on_stdout,
            ),
            monitor_stream(
                cast(asyncio.StreamReader, proc.stderr),
                collect=True,
                display=verbose,
            ),
            proc._feed_stdin(input.encode()) if input else empty_fut,  # type: ignore[attr-defined]
        )
        returncode = await proc.wait()
        if (
            returncode is not None
            and returncode != 0  # success
            and returncode != 130  # user interrupt
        ):
            sys.stdout.write(stdout.decode() if stdout else "")
            sys.stderr.write(stderr.decode() if stderr else "")
            raise click.exceptions.Exit(returncode)
        if collect:
            return (
                stdout.decode() if stdout else None,
                stderr.decode() if stderr else None,
            )
        else:
            return None, None
    finally:
        try:
            if proc.returncode is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
                except (ProcessLookupError, KeyboardInterrupt):
                    pass

            loop.remove_signal_handler(signal.SIGINT)
            loop.remove_signal_handler(signal.SIGTERM)
        except UnboundLocalError:
            pass


async def monitor_stream(
    stream: asyncio.StreamReader,
    collect: bool = False,
    display: bool = False,
    on_line: Optional[Callable[[str], Optional[bool]]] = None,
) -> Optional[bytearray]:
    if collect:
        ba = bytearray()

    def handle(line: bytes):
        nonlocal on_line
        nonlocal display

        if collect:
            ba.extend(line)
        if display:
            sys.stdout.write(line.decode())
        if on_line:
            if on_line(line.decode()):
                on_line = None
                display = True

    async for line in stream:
        await asyncio.to_thread(handle, line)
    if collect:
        return ba
    else:
        return None
