"""Knowledge keeps original evidence, and never recalls disabled/stale sources."""

import asyncio
import json

import pytest

from corki.config import CorkiSettings
from corki.knowledge.extraction import (
    batches,
    extract,
    extraction_key,
    request_batches,
    request_payload,
    validate,
)
from corki.knowledge.store import KnowledgeStore, digest
from corki.models.types import ModelCompleted, ModelTextDelta
from corki.skills.frontmatter import parse
from corki.skills.installer import bundled_skills_source


def document(tmp_path, text="# Budget\n\n预算不足会导致投放受限。\n\n设置 timeout_seconds。"):
    store = KnowledgeStore(tmp_path)
    path = store.root / "budget.md"
    path.parent.mkdir(parents=True)
    path.write_text(text)
    doc = store.sync(path, "Budget", text)
    return store, path, doc, text


def extracted(blocks, text, title="预算设置"):
    return validate(
        {
            "segments": [
                {
                    "start": 1,
                    "end": len(blocks),
                    "title": title,
                    "summary": "解释预算不足和投放受限，适合预算排查问题。",
                    "keywords": ["投放受限", "timeout_seconds"],
                }
            ]
        },
        blocks,
        text,
    )


def ready(store, doc, text):
    store.set_enabled(doc["id"], True)
    job = store.claim(doc["id"], "fixture/model")
    rows = [row for blocks in batches(text) for row in extracted(blocks, text)]
    assert store.finish(doc["id"], job, doc["hash"], rows)
    return store.search(document_id=doc["id"])["results"][0]["id"]


def test_identity_evidence_recall_and_disable(tmp_path):
    store, path, doc, text = document(tmp_path)
    assert not doc["enabled"]
    assert store.search()["total"] == 0
    segment = ready(store, doc, text)
    assert store.search("预算")["results"][0]["id"] == segment
    assert store.search("timeout_seconds")["total"] == 1
    assert store.read(segment)["content"] == text
    assert store.read(segment, limit=3)["next_offset"] == 3
    store.set_enabled(doc["id"], False)
    assert not store.search()["results"]
    with pytest.raises(ValueError, match="停用"):
        store.read(segment)
    store.set_enabled(doc["id"], True)
    assert store.claim(doc["id"], "different/model") is None
    assert store.read(segment)["content"] == text
    path.write_text("changed")
    assert not store.search()["results"]
    with pytest.raises(ValueError, match="更新"):
        store.read(segment)
    updated = store.sync(path, "Budget", "changed")
    assert updated["id"] == doc["id"] and updated["status"] == "pending"
    assert updated["summary"] == ""


def test_chunk_boundaries_preserve_source(tmp_path):
    text = "# 中文标题\n\n" + "中文段落。\n" * 3000 + "\n结尾"
    store, path, doc, _ = document(tmp_path, text)
    rows = [r for blocks in batches(text, 12000) for r in extracted(blocks, text)]
    assert "".join(r["content"] for r in rows) == text
    assert all(len(r["content"]) <= 12000 for r in rows)
    assert rows[0]["start_char"] == 0 and rows[-1]["end_char"] == len(text)
    for row in rows:
        assert text[row["start_char"] : row["end_char"]] == row["content"]
    for response in ({"segments": []}, {"segments": [{"start": 2, "end": 3}]}):
        with pytest.raises(ValueError):
            validate(response, next(batches(text)), text)


def test_deleted_or_disabled_job_cannot_publish(tmp_path):
    store, path, doc, text = document(tmp_path)
    store.set_enabled(doc["id"], True)
    job = store.claim(doc["id"], "fixture")
    assert store.claim(doc["id"], "fixture") is None
    rows = extracted(next(batches(text)), text)
    store.set_enabled(doc["id"], False)
    assert not store.finish(doc["id"], job, digest(text), rows)
    with pytest.raises(ValueError, match="取消"):
        store.save_batch(doc["id"], job, 0, rows)
    store.set_enabled(doc["id"], True)
    job = store.claim(doc["id"], "fixture")
    store.delete(doc["id"])
    assert not store.finish(doc["id"], job, digest(text), rows)


def test_skill_execution_repairs_invalid_output_and_closes(tmp_path):
    store, path, doc, text = document(tmp_path)
    store.set_enabled(doc["id"], True)
    job = store.claim(doc["id"], "fixture")
    requests = []

    class Model:
        closed = False

        async def stream(self, request):
            requests.append(request)
            if len(requests) == 1:
                yield ModelTextDelta("not-json")
            else:
                blocks = json.loads(request.items[0].content.split("\n上次输出")[0])["blocks"]
                yield ModelTextDelta(
                    json.dumps(
                        {
                            "segments": [
                                {
                                    "start": 1,
                                    "end": len(blocks),
                                    "title": "预算",
                                    "summary": "解释预算与超时设置。",
                                    "keywords": ["预算"],
                                }
                            ]
                        }
                    )
                )
            yield ModelCompleted(())

        async def aclose(self):
            self.closed = True

    model = Model()
    asyncio.run(extract(model, CorkiSettings(working_directory=tmp_path), store, doc, job, text))
    assert model.closed and len(requests) == 2
    assert "Corki Text C&E" in requests[0].instructions
    assert not requests[0].tools
    segment = store.search("预算")["results"][0]
    assert store.read(segment["id"])["content"] == text
    source = bundled_skills_source().joinpath("corki-text-C&E/SKILL.md").read_text()
    assert parse(source, "fallback")[0] == "corki-text-C&E"


def test_failed_batch_resume_and_pagination(tmp_path):
    store, path, doc, text = document(tmp_path, "第一部分\n\n" + "X" * 120000)
    store.set_enabled(doc["id"], True)
    job = store.claim(doc["id"], "fixture")
    settings = CorkiSettings(working_directory=tmp_path)
    skill = bundled_skills_source().joinpath("corki-text-C&E/SKILL.md").read_text()
    planned = list(request_batches(text, doc["title"], skill,
                                   settings.model_context_info(settings.model)))
    first = planned[0]
    cached = extracted(first, text)
    for row in cached:
        row["_extraction_key"] = extraction_key(
            skill, settings.model, request_payload(doc["title"], first, text))
    store.save_batch(doc["id"], job, 0, cached)
    store.fail(doc["id"], job, "interrupted")
    job = store.claim(doc["id"], "fixture")
    calls = []

    class Model:
        async def stream(self, request):
            calls.append(request)
            count = len(json.loads(request.items[0].content)["blocks"])
            yield ModelTextDelta(
                json.dumps(
                    {
                        "segments": [
                            {
                                "start": 1,
                                "end": count,
                                "title": "下一部分",
                                "summary": "长文本说明",
                                "keywords": [],
                            }
                        ]
                    }
                )
            )
            yield ModelCompleted(())

        async def aclose(self):
            pass

    asyncio.run(extract(Model(), CorkiSettings(working_directory=tmp_path), store, doc, job, text))
    assert len(calls) == len(planned) - 1
    page = store.search(document_id=doc["id"], limit=1)
    assert page["next_offset"] == 1
    rows = store.search(document_id=doc["id"], limit=30)["results"]
    assert "".join(store.read(r["id"], limit=200000)["content"] for r in rows) == text


def test_disconnect_never_publishes_partial_summary(tmp_path):
    store, path, doc, text = document(tmp_path)
    store.set_enabled(doc["id"], True)
    job = store.claim(doc["id"], "fixture")

    class Model:
        async def stream(self, request):
            yield ModelTextDelta('{"segments": []}')

        async def aclose(self):
            pass

    with pytest.raises(ValueError, match="中断"):
        asyncio.run(
            extract(Model(), CorkiSettings(working_directory=tmp_path), store, doc, job, text)
        )
    assert store.search()["total"] == 0


def test_orphan_processing_job_is_retryable(tmp_path, monkeypatch):
    store, path, doc, text = document(tmp_path)
    store.set_enabled(doc["id"], True)
    job = store.claim(doc["id"], "fixture")
    monkeypatch.setattr("corki.knowledge.store.process_alive", lambda _: False)
    refreshed = store.sync(path, doc["title"], text)
    assert refreshed["status"] == "failed" and refreshed["job"] is None
    assert store.claim(doc["id"], "fixture") != job


def test_whole_document_request_is_not_limited_by_block_count():
    from types import SimpleNamespace

    text = "# Manual\n\n" + "说明文字。\n\n" * 300
    info = SimpleNamespace(resolved_context_window=262144, effective_context_window_percent=95)
    planned = list(request_batches(text, "Manual", "C&E instructions", info))
    assert len(planned) == 1 and len(planned[0]) > 96
    assert "".join(text[a:b] for a, b in planned[0]) == text


def test_request_budget_preserves_fitting_chapters_and_fenced_examples():
    from types import SimpleNamespace

    from corki.context.tokens import estimate_text_tokens

    code = '```python\nvalue = 1\n\n## not a chapter\nprint(value)\n```\n\n'
    chapters = [f"## Topic {i}\n\n" + "中文说明。" * 200 + "\n\n" + code for i in range(12)]
    text = "".join(chapters)
    skill = "Preserve chapters"
    info = SimpleNamespace(resolved_context_window=12000, effective_context_window_percent=95)
    planned = list(request_batches(text, "Manual", skill, info))
    assert len(planned) > 1
    assert "".join(text[a:b] for batch in planned for a, b in batch) == text
    budget = int(12000 * .95 * .65) - 2 * estimate_text_tokens(skill) - 1024
    for batch in planned:
        payload = request_payload("Manual", batch, text)
        assert 2 * estimate_text_tokens(payload) <= budget
        source = text[batch[0][0]:batch[-1][1]]
        assert source.count('```python') == source.count('print(value)')
        assert source.startswith('## Topic ')
    for chapter in chapters:
        assert any(chapter in text[b[0][0]:b[-1][1]] for b in planned)


def test_too_small_context_fails_before_model_call():
    from types import SimpleNamespace

    info = SimpleNamespace(resolved_context_window=1024, effective_context_window_percent=95)
    with pytest.raises(ValueError, match="上下文容量不足"):
        list(request_batches("hello", "Manual", "C&E instructions", info))


def test_extraction_cache_key_tracks_skill_and_model():
    key = extraction_key("old skill", "model-a", "payload")
    assert key != extraction_key("new skill", "model-a", "payload")
    assert key != extraction_key("old skill", "model-b", "payload")
    assert key != extraction_key("old skill", "model-a", "changed payload")
