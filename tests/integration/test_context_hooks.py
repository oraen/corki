"""Prompt/Skill hooks inject context through the existing durable hook pipeline."""
import asyncio
import json
from dataclasses import asdict

import pytest

from corki.config import CorkiSettings
from corki.config.layers import ConfigLayer, LocalConfigState
from corki.core import LangGraphRuntime
from corki.core.stop_hooks import command_identity, discover, restore_command
from corki.models import ModelCompleted
from corki.protocol.items import ContextItem
from corki.tools import ToolRegistry


@pytest.mark.parametrize('kind', ['prompt', 'skill'])
@pytest.mark.parametrize('event', ['UserPromptSubmit', 'SessionStart'])
def test_context_hook_reaches_model_without_extra_sampling(tmp_path, kind, event):
    async def scenario():
        skill = tmp_path/'private'/'SKILL.md'
        skill.parent.mkdir()
        skill.write_text('---\nname: hook-skill\ndescription: test\n---\nCONTEXT_PROOF\nRead references/guide.md if needed.')
        handler = {'type':kind, **({'prompt':'CONTEXT_PROOF'} if kind=='prompt' else {'path':str(skill)})}
        fingerprint,_ = command_identity(handler,event_name=event,matcher='*')
        source=tmp_path/'settings.json'
        key='user_prompt_submit' if event=='UserPromptSubmit' else 'session_start'
        doc={'skills':{'config':[{'path':str(skill),'enabled':False}]},'hooks':{event:[{'matcher':'*','hooks':[handler]}], 'state':{f'{source}:{key}:0:0':{'trusted_hash':fingerprint}}}}
        config=LocalConfigState((ConfigLayer(source,'user',contents=json.dumps(doc)),))
        commands,_=discover(config,event_name=event)
        assert restore_command(asdict(commands[0]))==commands[0]
        requests=[]
        class Model:
            async def stream(self,request):
                requests.append(request)
                yield ModelCompleted(())
            async def aclose(self):pass
        runtime=await LangGraphRuntime.acreate(settings=CorkiSettings(tmp_path,skills_enabled=False,plugins_enabled=False,configuration=config),model=Model(),registry=ToolRegistry(),database_path=tmp_path/'state.db',home_path=tmp_path)
        try:
            _=[e async for e in runtime.stream('hello')]
            assert len(requests)==1
            contexts=[i for i in requests[0].items if isinstance(i,ContextItem) and 'CONTEXT_PROOF' in i.content]
            assert len(contexts)==1
            if kind=='skill':assert str(skill.parent) in contexts[0].content
        finally:await runtime.aclose()
    asyncio.run(scenario())


def test_context_hooks_unsupported_events_and_disabled_skills(tmp_path):
    with pytest.raises(ValueError):command_identity({'type':'prompt','prompt':'hello'},event_name='Stop')
    with pytest.raises(ValueError):command_identity({'type':'prompt','prompt':''},event_name='SessionStart')
    path=tmp_path/'SKILL.md';path.write_text('---\nname: fixture\ndescription: test\n---\nBody')
    handler={'type':'skill','path':str(path)}
    fingerprint,_=command_identity(handler,event_name='UserPromptSubmit',matcher='*')
    source=tmp_path/'settings.json'
    doc={'skills':{'config':[{'path':str(path),'enabled':False}]},'hooks':{'UserPromptSubmit':[{'matcher':'*','hooks':[handler]}],'state':{f'{source}:user_prompt_submit:0:0':{'trusted_hash':fingerprint}}}}
    commands,warnings=discover(LocalConfigState((ConfigLayer(source,'user',contents=json.dumps(doc)),)),event_name='UserPromptSubmit')
    assert len(commands) == 1 and not warnings
    doc['hooks']['state'][f'{source}:user_prompt_submit:0:0']['enabled'] = False
    commands, warnings = discover(LocalConfigState((ConfigLayer(source,'user',contents=json.dumps(doc)),)), event_name='UserPromptSubmit')
    assert not commands and not warnings
