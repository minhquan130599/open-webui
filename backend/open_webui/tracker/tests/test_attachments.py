"""Attachments (files, images), deletions and the P3 tools. See test_tracker.py for how to run."""

import base64
import io
import os
import time
import uuid

import pytest
import pytest_asyncio

from open_webui.tracker import llm, media, service
from open_webui.tracker.schema import StateUpdate


def test_split_chunks_samples_long_documents():
    content = '\n\n'.join(f'Đoạn {i} ' + 'x' * 900 for i in range(40))
    chunks, total = media.split_chunks(content, chunk_chars=2_000, max_chunks=5)
    assert total > 5 and len(chunks) == 5
    assert all(len(c) <= 2_000 for c in chunks)
    assert chunks[0].startswith('Đoạn 0')

    one, total = media.split_chunks('ngắn', 2_000, 5)
    assert one == ['ngắn'] and total == 1


def test_downscale_image():
    from PIL import Image

    buffer = io.BytesIO()
    Image.new('RGBA', (3000, 1500), (200, 10, 10, 255)).save(buffer, format='PNG')
    uri = media.downscale_image(buffer.getvalue(), 1024)
    assert uri.startswith('data:image/jpeg;base64,')
    with Image.open(io.BytesIO(base64.b64decode(uri.split(',', 1)[1]))) as out:
        assert max(out.size) == 1024


@pytest_asyncio.fixture(loop_scope='module')
async def ctx():
    from open_webui.models.channels import Channels, CreateChannelForm
    from open_webui.models.users import Users

    owner = await Users.insert_new_user(str(uuid.uuid4()), 'Minh', f'{uuid.uuid4().hex}@test.local', role='admin')
    other = await Users.insert_new_user(str(uuid.uuid4()), 'Khác', f'{uuid.uuid4().hex}@test.local', role='user')
    channel = await Channels.insert_new_channel(CreateChannelForm(name=f'XD-{uuid.uuid4().hex[:6]}'), owner.id)
    return owner, other, channel


async def _file(
    user_id: str,
    name: str,
    *,
    content: str = '',
    status: str = 'completed',
    path: str = '',
    content_type: str = 'application/pdf',
    file_hash: str | None = None,
):
    from open_webui.models.files import FileForm, Files

    file_id = str(uuid.uuid4())
    return await Files.insert_new_file(
        user_id,
        FileForm(
            id=file_id,
            filename=name,
            path=path or f'/nonexistent/{file_id}',
            data={'status': status, **({'content': content} if content else {})},
            meta={'name': name, 'content_type': content_type, 'file_hash': file_hash or uuid.uuid4().hex},
        ),
    )


async def _post(channel_id: str, user_id: str, content: str, files: list | None = None):
    from open_webui.models.messages import MessageForm, Messages
    from open_webui.tracker.models import Trackers

    message = await Messages.insert_new_message(
        MessageForm(content=content, data={'files': files} if files else None), channel_id, user_id
    )
    await Trackers.mark_dirty(channel_id)
    time.sleep(0.002)
    return message


async def _run(channel_id: str):
    from open_webui.tracker.models import Trackers

    claimed = [c for c in await Trackers.claim_due(time.time_ns(), 1000, 0, 60 * 10**9, 100) if c[0] == channel_id]
    assert claimed, 'channel should be due'
    await service.process_channel(*claimed[0])


def _fake_update(prompts: list):
    async def fake(messages):
        prompts.append(messages[-1]['content'])
        return StateUpdate.model_validate(
            {
                'trang_thai': {'tong_quan': 'ok', 'hang_muc': [{'ten': 'Móng nhà', 'tien_do': 80, 'nguon': ['m1']}]},
                'tom_tat_lo': 'Cập nhật móng nhà',
            }
        )

    return fake


@pytest.mark.asyncio(loop_scope='module')
async def test_file_summary_is_used_and_cached(ctx, monkeypatch):
    from open_webui.models.channels import Channels, CreateChannelForm
    from open_webui.tracker.models import Trackers

    owner, _, channel = ctx
    prompts, text_calls = [], []

    async def fake_text(messages, max_tokens=1024):
        text_calls.append(messages)
        return '- Móng nhà: hoàn thành 80%, hạn 2026-12-01'

    monkeypatch.setattr(llm, 'generate_text', fake_text)
    monkeypatch.setattr(llm, 'generate_state_update', _fake_update(prompts))

    shared_hash = uuid.uuid4().hex
    report = await _file(owner.id, 'bao_cao.pdf', content='Báo cáo tuần: móng nhà 80%', file_hash=shared_hash)
    await _post(channel.id, owner.id, 'Gửi báo cáo tuần', [{'type': 'file', 'id': report.id, 'name': 'bao_cao.pdf'}])
    await _run(channel.id)

    assert '↳ bao_cao.pdf: - Móng nhà: hoàn thành 80%' in prompts[-1]
    assert len(text_calls) == 1
    assert (await Trackers.get_cached_item(f'file:{shared_hash}')).summary.startswith('- Móng nhà')

    # Same content posted in another channel: summary comes from the cache, no new LLM call.
    second = await Channels.insert_new_channel(CreateChannelForm(name='XD-2'), owner.id)
    copy = await _file(owner.id, 'bao_cao (1).pdf', content='Báo cáo tuần: móng nhà 80%', file_hash=shared_hash)
    await _post(second.id, owner.id, 'Báo cáo', [{'type': 'file', 'id': copy.id, 'name': 'bao_cao (1).pdf'}])
    await _run(second.id)
    assert len(text_calls) == 1 and '↳ bao_cao (1).pdf: - Móng nhà' in prompts[-1]

    # Evidence search finds the file summary without accents; timeline lists the update.
    from open_webui.tracker.tools import get_project_timeline, search_project_evidence

    found = await search_project_evidence(channel.name, 'mong nha', __user__={'id': owner.id})
    assert 'bao_cao.pdf' in found
    assert 'Cập nhật móng nhà' in await get_project_timeline(channel.name, 7, __user__={'id': owner.id})


@pytest.mark.asyncio(loop_scope='module')
async def test_waits_for_file_extraction(ctx, monkeypatch):
    from open_webui.tracker.models import Trackers

    owner, _, channel = ctx
    prompts = []
    monkeypatch.setattr(llm, 'generate_state_update', _fake_update(prompts))

    before = await _post(channel.id, owner.id, 'Đổ bê tông xong tầng 1')
    pending = await _file(owner.id, 'nhat_ky.docx', status='processing')
    blocked = await _post(channel.id, owner.id, 'Nhật ký', [{'type': 'file', 'id': pending.id, 'name': 'nhat_ky.docx'}])
    await _run(channel.id)

    row = await Trackers.get_state(channel.id)
    assert 'Đổ bê tông' in prompts[-1] and 'Nhật ký' not in prompts[-1]
    assert before.created_at <= row.watermark < blocked.created_at
    assert row.next_attempt_at and row.next_attempt_at > time.time_ns(), 'retry later, not in a hot loop'
    assert not row.force


@pytest.mark.asyncio(loop_scope='module')
async def test_image_is_described_and_foreign_files_are_skipped(ctx, monkeypatch):
    from PIL import Image

    from open_webui.config import UPLOAD_DIR

    owner, other, channel = ctx
    prompts, image_payloads = [], []

    async def fake_text(messages, max_tokens=1024):
        image_payloads.append(messages[0]['content'])
        return 'Ảnh chụp công trường, cột tầng 2 đã dựng'

    monkeypatch.setattr(llm, 'generate_text', fake_text)
    monkeypatch.setattr(llm, 'generate_state_update', _fake_update(prompts))

    path = os.path.join(UPLOAD_DIR, f'{uuid.uuid4()}.png')
    Image.new('RGB', (1600, 1200), (90, 90, 90)).save(path)
    photo = await _file(owner.id, 'cong_truong.png', path=path, content_type='image/png')
    foreign = await _file(other.id, 'bi_mat.pdf', content='Lương nhân viên')  # not the author's, not linked

    await _post(
        channel.id,
        owner.id,
        'Ảnh hôm nay',
        [
            {'type': 'image', 'id': photo.id, 'name': 'cong_truong.png', 'content_type': 'image/png'},
            {'type': 'file', 'id': foreign.id, 'name': 'bi_mat.pdf'},
        ],
    )
    await _run(channel.id)

    image_part = next(p for p in image_payloads[0] if p['type'] == 'image_url')
    assert image_part['image_url']['url'].startswith('data:image/jpeg;base64,')
    assert '↳ cong_truong.png: Ảnh chụp công trường' in prompts[-1]
    assert 'bi_mat' not in prompts[-1].split('↳')[-1] and 'Lương' not in prompts[-1]


@pytest.mark.asyncio(loop_scope='module')
async def test_deleted_message_is_retracted(ctx, monkeypatch):
    from open_webui.models.messages import Messages
    from open_webui.tracker.models import Trackers

    owner, _, channel = ctx
    prompts = []
    monkeypatch.setattr(llm, 'generate_state_update', _fake_update(prompts))

    message = await _post(channel.id, owner.id, 'Móng nhà xong 100%')
    await _run(channel.id)
    assert f'msg:{message.id}' in (await Trackers.get_state(channel.id)).state['hang_muc'][0]['nguon']

    await Trackers.add_deletion(
        channel.id,
        {'id': message.id, 'user_name': 'Minh', 'content': message.content, 'created_at': message.created_at},
    )
    await Messages.delete_message_by_id(message.id)
    await _run(channel.id)

    row = await Trackers.get_state(channel.id)
    assert '(đã xóa)' in prompts[-1] and 'Móng nhà xong 100%' in prompts[-1]
    assert not row.pending_deletions and row.dirty_at is None
    assert all(f'msg:{message.id}' not in e['nguon'] for e in row.state['hang_muc'])
