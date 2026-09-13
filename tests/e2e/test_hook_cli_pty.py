"""Real trusted Stop process through graph, CLI event owner and physical terminal."""

import os
import sys

import pexpect
import pytest

BOOTSTRAP = r"""
import asyncio, json, os, shlex, sys
from pathlib import Path
from corki.cli.application import CorkiApplication
from corki.cli.terminal import TerminalUI
from corki.config import CorkiPaths, CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity
from corki.models import ModelCompleted
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, ContextItem, UserMessageItem, new_step_id
from corki.sessions import TurnRecord, TurnStatus

async def main():
    cwd = Path.cwd()
    mode = sys.argv[1]
    realtime = sys.argv[2] == 'true'
    resume = sys.argv[3] == 'resume'
    script = '''import json, os, sys, time
from pathlib import Path
payload = json.load(sys.stdin)
Path('hook-pid').write_text(str(os.getpid()))
with Path('hook-calls').open('a') as out:
    out.write(str(payload['stop_hook_active']) + '\\n')
while not Path('release-hook').exists():
    time.sleep(0.02)
print(json.dumps({'decision': 'block', 'reason': 'CHECK_AGAIN'}
    if sys.argv[1] == 'block' and not payload['stop_hook_active'] else {}))
'''
    handler = {'type': 'command', 'command': shlex.join([sys.executable, '-c', script, mode]),
               'statusMessage': 'HOOK_CHECKING', 'timeout': 15}
    fingerprint, _ = command_identity(handler)
    source = cwd / 'user.toml'
    document = ('[[hooks.Stop]]\n[[hooks.Stop.hooks]]\n'
        + '\n'.join(f'{key}={json.dumps(value)}' for key, value in handler.items())
        + f'\n[hooks.state.{json.dumps(str(source) + ":stop:0:0")}]\n'
        + f'trusted_hash={json.dumps(fingerprint)}\nenabled=true\n')
    settings = CorkiSettings(cwd, skills_enabled=False, plugins_enabled=False,
        realtime_enabled=realtime, configuration=LocalConfigState((
            ConfigLayer(source, 'user', contents=document),)))
    class Model:
        count = 0
        async def stream(self, request):
            self.count += 1
            assert self.count <= (2 if mode == 'block' else 1)
            if self.count == 2:
                assert any(isinstance(i, ContextItem) and i.content == 'CHECK_AGAIN'
                           for i in request.items)
            yield ModelCompleted((AssistantMessageItem(
                'ANSWER_READY', request.items[-1].turn_id, new_step_id()),))
        async def aclose(self): pass
    model = Model()
    runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
        database_path=cwd / 'session.db', home_path=cwd / 'home')
    if resume:
        await runtime._ensure_ready()
        thread, turn = runtime.thread_id, new_turn_id()
        await runtime._repository.save_turn(TurnRecord(turn, thread, TurnStatus.RUNNING, 'finish'))
        await runtime._repository.append_items(thread, (UserMessageItem('finish', turn),))
        await runtime.aclose()
        runtime = await LangGraphRuntime.acreate(settings=settings, model=model,
            database_path=cwd / 'session.db', home_path=cwd / 'home', thread_id=thread)
    ui = TerminalUI(settings, cwd / 'input-history')
    class App(CorkiApplication):
        async def _consume_interactive_events(self, events, **kwargs):
            try:
                await super()._consume_interactive_events(events, **kwargs)
            finally:
                if model.count:
                    await self.verify_completion()
        async def verify_completion(self):
                assert ui._hook_timer is None
                assert ui._hook_activity.summary is ui._hook_activity.deadline is None
                transcript = ui._transcript.render(100)
                assert 'HOOK_CHECKING' not in transcript
                assert ('Blocked by hook' in transcript) == (mode == 'block')
                assert ('Hook completed' in transcript) is False
                assert model.count == (2 if mode == 'block' else 1)
                items = await runtime._repository.load_items(runtime.thread_id)
                assert len([i for i in items if isinstance(i, UserMessageItem)]) == 1
                if mode == 'cancel':
                    try:
                        os.kill(int((cwd / 'hook-pid').read_text()), 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise AssertionError('cancel left the Stop process alive')
                print('TURN_RETURNED', flush=True)
    app = App(settings, CorkiPaths.from_home(cwd / 'home'), runtime, ui)
    assert await app.run() == 0
    assert list(ui._session.history.get_strings()) == ([] if resume else ['finish'])
    print('RESULT_OK', flush=True)
asyncio.run(main())
"""


@pytest.mark.parametrize("width", [40, 100])
@pytest.mark.parametrize("mode", ["quiet", "block", "cancel"])
@pytest.mark.parametrize("realtime", ["true", "false"])
@pytest.mark.parametrize("entry", ["fresh", "resume"])
def test_hook_activity_completion_and_cancel_in_real_terminal(
    tmp_path, width, mode, realtime, entry
):
    child = pexpect.spawn(
        sys.executable,
        ["-c", BOOTSTRAP, mode, realtime, entry],
        cwd=str(tmp_path),
        env={
            **{key: value for key, value in os.environ.items() if key != "NO_COLOR"},
            "CORKI_HOME": str(tmp_path / "home"),
            "PYTHON_KEYRING_BACKEND": "keyring.backends.null.Keyring",
            "TERM": "xterm-256color",
            "PROMPT_TOOLKIT_NO_CPR": "0",
        },
        dimensions=(30, width),
        encoding="utf-8",
        timeout=20,
    )

    def expect_rendered(text):
        while child.expect_exact([text, "\x1b[6n"]) == 1:
            child.send("\x1b[1;1R")

    try:
        if entry == "fresh":
            expect_rendered("Ask Corki to do anything")
            child.sendline("finish")
        expect_rendered("ANSWER_READY")
        expect_rendered("HOOK_CHECKING")
        if mode == "cancel":
            child.sendcontrol("c")
        else:
            (tmp_path / "release-hook").touch()
            if mode == "block":
                expect_rendered("Blocked by hook")
                assert "\x1b[1;31m•\x1b[0m " in child.before
                expect_rendered("CHECK_AGAIN")
        expect_rendered("TURN_RETURNED")
        expect_rendered("shift+tab to cycle")
        child.sendcontrol("d")
        expect_rendered("RESULT_OK")
        child.expect(pexpect.EOF)
        child.close()
        assert child.exitstatus == 0
        assert (tmp_path / "hook-calls").read_text().splitlines() == (
            ["False", "True"] if mode == "block" else ["False"]
        )
    finally:
        if child.isalive():
            child.close(force=True)
