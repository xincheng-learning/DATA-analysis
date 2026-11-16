"""Serve an isolated QA chat store; real model calls are deliberately disabled."""
import argparse
import asyncio
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


async def serve(directory, port):
    import uvicorn
    from app import llm, main, superset
    from app.config import settings

    directory = directory.resolve()
    assert any(directory.is_relative_to(ROOT / parent) for parent in ('.local', 'evals/runs'))
    assert directory != settings.local_data_dir.resolve(), 'Never use the original chat store for QA'
    directory.mkdir(parents=True, exist_ok=True)
    original = settings.local_data_dir
    for name in ('model-settings.bin', 'database-settings.bin'):
        if (original / name).exists() and not (directory / name).exists():
            shutil.copyfile(original / name, directory / name)
    settings.local_data_dir = directory
    settings.app_mode = 'local'
    settings.session_backend = 'sqlite'
    superset.runtime_dir = lambda: original / 'superset'

    def no_paid_requests(*args, **kwargs):
        raise llm.LLMConfigurationError('截图与接口验收服务禁止收费模型调用；真实模型另用累计预算脚本验证。')
    llm.build_compatible_client = no_paid_requests
    llm.reset_cache()
    stop = directory / 'qa.stop'
    stop.unlink(missing_ok=True)
    server = uvicorn.Server(uvicorn.Config(main.app, host='127.0.0.1', port=port, access_log=False))

    async def watch_stop():
        while not server.should_exit:
            if stop.exists():
                server.should_exit = True
            await asyncio.sleep(.5)
    watcher = asyncio.create_task(watch_stop())
    try:
        await server.serve()
    finally:
        server.should_exit = True
        await watcher
        main.tasks.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8101)
    args = parser.parse_args()
    asyncio.run(serve(args.directory, args.port))
