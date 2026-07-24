from __future__ import annotations

import asyncio
import logging
import shlex
import time
from pathlib import Path

from presentation_video.domain.errors import MissingMediaDependencyError, UserFacingError

logger = logging.getLogger(__name__)


class ProcessExecutionError(UserFacingError):
    pass


async def run_process(
    *args: str,
    cwd: Path | None = None,
    timeout_seconds: float = 600,
) -> str:
    command = shlex.join(args)
    command_summary = command if len(command) <= 1_500 else command[:1_500] + "..."
    started_at = time.monotonic()
    logger.info(
        "process starting executable=%s timeout_seconds=%s command=%s",
        args[0] if args else "unknown",
        timeout_seconds,
        command_summary,
    )
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(cwd) if cwd else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        executable = args[0] if args else "unknown"
        raise MissingMediaDependencyError(
            f"Required media executable was not found: {executable}",
            "O servidor não possui todas as ferramentas necessárias para gerar áudio e vídeo. "
            "Peça ao administrador para instalar eSpeak NG e FFmpeg.",
        ) from exc
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout_seconds,
        )
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        logger.error(
            "process timed out executable=%s pid=%s elapsed_seconds=%.1f command=%s",
            args[0] if args else "unknown",
            process.pid,
            time.monotonic() - started_at,
            command_summary,
        )
        raise ProcessExecutionError(
            f"Command timed out after {timeout_seconds}s: {command_summary}",
            "A renderização excedeu o tempo máximo e foi interrompida. Tente novamente; "
            "se o problema continuar, reduza a duração ou a quantidade de cenas.",
        ) from exc
    if process.returncode != 0:
        stderr_text = stderr.decode(errors="replace")
        logger.error(
            "process failed executable=%s pid=%s returncode=%s elapsed_seconds=%.1f stderr=%s",
            args[0] if args else "unknown",
            process.pid,
            process.returncode,
            time.monotonic() - started_at,
            stderr_text[-4_000:],
        )
        raise ProcessExecutionError(
            f"Command failed ({process.returncode}): {command_summary}\n{stderr_text}",
            "Não foi possível processar uma das cenas do vídeo. Consulte os logs do servidor "
            "para identificar o comando de mídia que falhou.",
        )
    logger.info(
        "process completed executable=%s pid=%s elapsed_seconds=%.1f stdout_bytes=%s "
        "stderr_bytes=%s",
        args[0] if args else "unknown",
        process.pid,
        time.monotonic() - started_at,
        len(stdout),
        len(stderr),
    )
    return stdout.decode(errors="replace")
