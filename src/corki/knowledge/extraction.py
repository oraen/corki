"""Run the bundled C&E skill through Corki's configured model adapter."""

import asyncio
import json
import re

from corki.context.tokens import estimate_text_tokens
from corki.knowledge.store import digest
from corki.models.types import ModelCompleted, ModelRequest, ModelTextDelta
from corki.protocol.ids import new_turn_id
from corki.protocol.items import AssistantMessageItem, UserMessageItem
from corki.skills.installer import install_bundled_skills


def batches(content, max_chars=None):
    """Preserve every character; prefer blank-line boundaries, bound huge paragraphs."""
    max_chars = max_chars or max(1, len(content))
    blocks = []
    start = 0
    position, fence = 0, None
    for line in content.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        position += len(line)
        if not line.strip() and fence is None:
            blocks.append((start, position))
            start = position
    if start < len(content):
        blocks.append((start, len(content)))
    bounded = []
    for start, end in blocks:
        while end - start > max_chars:
            boundary = content.rfind("\n", start + max_chars // 2, start + max_chars)
            stop = boundary + 1 if boundary >= 0 else start + max_chars
            bounded.append((start, stop))
            start = stop
        if start < end:
            bounded.append((start, end))
    batch = []
    for block in bounded:
        if batch and (block[1] - batch[0][0] > max_chars):
            yield batch
            batch = []
        batch.append(block)
    if batch:
        yield batch


def request_payload(title, blocks, content):
    return json.dumps({"title": title, "blocks": [
        {"id": i + 1, "text": content[a:b]} for i, (a, b) in enumerate(blocks)
    ]}, ensure_ascii=False)


def request_batches(content, title, skill, info):
    """Prefer whole-document context; split only to fit the configured model budget.

    Input blocks are coordinates, not semantic segments. Complete H2 chapters
    stay together when possible. Only the model produces the final index.
    """
    window = info.resolved_context_window or 32000
    effective = window * info.effective_context_window_percent // 100
    # Token counts are estimates, so double the UTF-8 estimate and reserve 35%
    # for output/reasoning plus protocol/correction overhead.
    budget = int(effective * .65) - 2 * estimate_text_tokens(skill) - 1024
    if budget < 512:
        raise ValueError("模型上下文容量不足以执行知识整理，请选择更大窗口的默认模型")

    def fits(blocks):
        return 2 * estimate_text_tokens(request_payload(title, blocks, content)) <= budget

    blocks = next(batches(content), [])
    if not blocks:
        return
    if fits(blocks):
        yield blocks
        return
    chapters, chapter = [], []
    for block in blocks:
        if chapter and re.match(r"## +", content[block[0]:block[1]].lstrip()):
            chapters.append(chapter)
            chapter = []
        chapter.append(block)
    if chapter:
        chapters.append(chapter)
    units = []
    for chapter in chapters:
        if fits(chapter):
            units.append(chapter)
            continue
        for start, end in chapter:
            while start < end:
                if fits([(start, end)]):
                    units.append([(start, end)])
                    break
                low, high = start, end
                while low + 1 < high:
                    middle = (low + high) // 2
                    if fits([(start, middle)]):
                        low = middle
                    else:
                        high = middle
                if low == start:
                    raise ValueError("模型上下文容量不足以容纳文档")
                boundary = content.rfind("\n", start + (low - start) // 2, low)
                stop = boundary + 1 if boundary >= 0 else low
                units.append([(start, stop)])
                start = stop
    batch = []
    for unit in units:
        if batch and not fits(batch + unit):
            yield batch
            batch = []
        batch.extend(unit)
    if batch:
        yield batch


def extraction_key(skill, model, payload):
    return digest("ce-topics-v2\n" + skill + "\n" + model + "\n" + payload)


def validate(value, blocks, content):
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list):
        raise ValueError("模型未返回有效的段落列表")
    rows, expected = [], 1
    for row in value["segments"]:
        if not isinstance(row, dict):
            raise ValueError("模型返回了无效段落")
        start, end = row.get("start"), row.get("end")
        if (
            type(start) is not int
            or type(end) is not int
            or start != expected
            or not start <= end <= len(blocks)
        ):
            raise ValueError("模型段落边界不连续或超出原文")
        for key, maximum in (("title", 120), ("summary", 900)):
            if not isinstance(row.get(key), str) or not 1 <= len(row[key].strip()) <= maximum:
                raise ValueError("模型段落标题或摘要格式无效")
        keywords = row.get("keywords")
        if (
            not isinstance(keywords, list)
            or len(keywords) > 16
            or any(not isinstance(k, str) or not 1 <= len(k.strip()) <= 80 for k in keywords)
        ):
            raise ValueError("模型段落关键词格式无效")
        a, b = blocks[start - 1][0], blocks[end - 1][1]
        rows.append(
            {
                "title": row["title"].strip(),
                "summary": row["summary"].strip(),
                "keywords": keywords,
                "content": content[a:b],
                "start_char": a,
                "end_char": b,
                "start_line": content.count("\n", 0, a) + 1,
                "end_line": content.count("\n", 0, max(a, b - 1)) + 1,
            }
        )
        expected = end + 1
    if expected != len(blocks) + 1:
        raise ValueError("模型段落未覆盖全部原文")
    return rows


def extraction_skill(home):
    path = home / "skills/work/corki-text-C&E/SKILL.md"
    if not path.is_file():
        install_bundled_skills(home)
    if not path.is_file():
        raise ValueError("工作 Skill corki-text-C&E 不存在，请重新导入后重试")
    return path.read_text(encoding="utf-8")


async def extract(model, settings, store, doc, job, content):
    results = []
    try:
        skill = await asyncio.to_thread(extraction_skill, store.home)
        info = settings.model_context_info(settings.model)
        async with asyncio.timeout(1200):
            for index, blocks in enumerate(request_batches(content, doc["title"], skill, info)):
                store.check_job(doc["id"], job)
                payload = request_payload(doc["title"], blocks, content)
                key = extraction_key(skill, settings.model, payload)
                cached = store.cached_batch(doc["id"], index)
                if cached and all(row.get("_extraction_key") == key for row in cached):
                    results.extend(cached)
                    continue
                error = ""
                # One bounded correction request for malformed JSON or uncovered source.
                for attempt in range(2):
                    store.check_job(doc["id"], job)
                    request = ModelRequest(
                        model=settings.model,
                        instructions=skill,
                        context_items=(),
                        items=(
                            UserMessageItem(
                                content=payload
                                + ("\n上次输出无效，请重新处理：" + error if error else ""),
                                turn_id=new_turn_id(),
                            ),
                        ),
                        tools=(),
                        reasoning_effort=settings.reasoning_effort,
                        model_info=settings.model_context_info(settings.model),
                    )
                    text, completed = "", False
                    async with asyncio.timeout(600):
                        async for event in model.stream(request):
                            if isinstance(event, ModelTextDelta):
                                text += event.delta
                            elif isinstance(event, ModelCompleted):
                                completed = True
                                final = "".join(
                                    i.content
                                    for i in event.items
                                    if isinstance(i, AssistantMessageItem)
                                )
                                if final:
                                    text = final
                    if not completed:
                        raise ValueError("模型响应中断，未完成摘要生成")
                    try:
                        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
                        rows = validate(json.loads(cleaned), blocks, content)
                        break
                    except (ValueError, TypeError) as exc:
                        error = str(exc)
                        if attempt:
                            raise ValueError("摘要校验失败：" + error) from exc
                for row in rows:
                    row["_extraction_key"] = key
                store.save_batch(doc["id"], job, index, rows)
                results.extend(rows)
            if not results:
                raise ValueError("文档内容为空")
            store.finish(doc["id"], job, doc["hash"], results)
    finally:
        await model.aclose()
