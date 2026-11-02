"""DingTalk message recall uses saved platform receipts, never model history IDs."""
import asyncio
import json
import re
from datetime import UTC, datetime

from .errors import DeliveryError


def receipt(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if not isinstance(value, dict) or value.get('complete') is not True:
        return None
    robot, keys = value.get('robot_code'), value.get('keys')
    if not isinstance(robot, str) or not 1 <= len(robot) <= 300:
        return None
    if not isinstance(keys, list) or not 1 <= len(keys) <= 200:
        return None
    if any(not isinstance(k, str) or not 1 <= len(k) <= 1024 for k in keys):
        return None
    return {'robot_code': robot, 'keys': list(dict.fromkeys(keys)), 'complete': True}


def public_message(row):
    row = dict(row)
    saved = receipt(row.pop('recall_receipt', None))
    state = row.get('recall_state', '')
    row['can_recall'] = bool(saved and row['role'] == 'assistant' and row['status'] == 'sent'
                              and state not in ('recalling', 'recalled'))
    return row


async def recall_message(host, data):
    channel, session, identity = (data.get(k) for k in ('id', 'session', 'message_id'))
    if not isinstance(identity, str) or not re.fullmatch(r'dtmsg:[1-9][0-9]{0,18}', identity):
        raise ValueError('消息标识无效')
    message_id = int(identity[6:])
    if message_id >= 2**63:
        raise ValueError('消息标识无效')
    account = host.store.connection(channel)
    if not account or account['type_id'] != 'dingtalk':
        raise ValueError('此接入不支持撤回')
    peer = host.store.messages.peer(channel, session)
    with host.store.db() as db:
        row = db.execute('SELECT * FROM dingtalk_messages WHERE id=? AND channel=? AND sender=?',
                         (message_id, channel, peer['sender'])).fetchone()
    if not row or row['role'] != 'assistant':
        raise ValueError('只能撤回当前联系人的自方消息')
    if row['recall_state'] in ('recalled', 'recalling'):
        return {'message': public_message(row)}
    saved = receipt(row['recall_receipt'])
    if row['status'] != 'sent' or not saved:
        raise ValueError('此消息没有可用的撤回凭据')
    if host.stopping or channel not in host.connectors or not account['enabled']:
        raise ValueError('请先开启网关并启用此接入')
    if len(host.manual_tasks) >= 20:
        raise ValueError('消息操作较多，请稍后重试')
    # Reserve before yielding. Multiple pages cannot start the same recall twice.
    with host.store.db() as db:
        claimed = db.execute("UPDATE dingtalk_messages SET recall_state='recalling',recall_error='' "
                             "WHERE id=? AND recall_state NOT IN ('recalling','recalled')",
                             (message_id,)).rowcount
    if not claimed:
        raise ValueError('消息状态已变化，请刷新后重试')
    key = (channel, 'recall:' + str(message_id))

    async def deliver():
        try:
            result = await host.connectors[channel].call('recall', {'receipt': saved}, timeout=60)
            confirmed = result.get('recalled', []) if isinstance(result, dict) else []
            if not isinstance(confirmed, list) or any(not isinstance(k, str) for k in confirmed):
                confirmed = []
            remaining = [k for k in saved['keys'] if k not in confirmed]
            state = 'recalled' if not remaining else 'partial' if len(remaining) < len(saved['keys']) else 'failed'
            error = '' if not remaining else str(result.get('error') or '钉钉未确认撤回成功')[:2000] if isinstance(result, dict) else '钉钉未确认撤回成功'
            with host.store.db() as db:
                db.execute('UPDATE dingtalk_messages SET recall_state=?,recall_error=?,recalled_at=?,recall_receipt=? WHERE id=?',
                           (state, error, datetime.now(UTC).isoformat() if not remaining else '',
                            json.dumps({**saved, 'keys': remaining or saved['keys']}), message_id))
        except (Exception, asyncio.CancelledError) as error:
            detail = str(error) if isinstance(error, DeliveryError) else '撤回失败或中断，结果未确认，请稍后重试'
            with host.store.db() as db:
                db.execute("UPDATE dingtalk_messages SET recall_state='failed',recall_error=? WHERE id=?",
                           (detail[:2000], message_id))
        finally:
            host.manual_tasks.pop(key, None)

    task = asyncio.create_task(deliver())
    host.manual_tasks[key] = task
    await asyncio.shield(task)
    with host.store.db() as db:
        row = db.execute('SELECT * FROM dingtalk_messages WHERE id=?', (message_id,)).fetchone()
    if row is None:
        raise ValueError('此消息所属的接入已删除，请刷新后查看')
    return {'message': public_message(row)}
