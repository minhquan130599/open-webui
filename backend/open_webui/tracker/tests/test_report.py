"""Report tests: completeness across parts, the coverage check, per-person statistics and file export.

Run from backend/ like the other tracker tests (DATA_DIR pointing to a scratch directory).
"""

import io
import time
import uuid

import pytest

from open_webui.tracker import report as reports
from open_webui.tracker.export import build_file
from open_webui.tracker.report import ProjectData, Report, ReportItem

TODAY = '2026-11-12'


def _item(n: int, project: str = 'OSINT3', **extra) -> ReportItem:
    values = {
        'ref': f'R{n}',
        'project': project,
        'ten': f'Hạng mục số {n} xây dựng chức năng',
        'phu_trach': ['Long' if n % 2 else 'Triển'],
        'trang_thai': 'dang_lam',
        'tien_do': 50,
        'han': '2026-11-20',
        'vuong_mac': None,
        'tao_luc': '2026-11-01',
        'cap_nhat_luc': TODAY,
        'hoan_thanh_luc': None,
    }
    values.update(extra)
    return ReportItem(**values)


def _report(items_by_project: dict[str, list[ReportItem]]) -> Report:
    projects = [
        ProjectData(
            id=name,
            name=name,
            tong_quan='Đang triển khai',
            items=items,
            timeline=[('2026-11-11', 'Long hoàn thành API.')],
            decisions=['Chốt dùng PostgreSQL'],
            risks=['[cao] Thiếu tài liệu'],
            next_steps=['Kiểm thử'],
            closed_count=0,
            processed_at=None,
            pending=0,
        )
        for name, items in items_by_project.items()
    ]
    report = Report(id=uuid.uuid4().hex[:10], title='Test', period_label='7 ngày', today=TODAY, projects=projects)
    reports._reports[report.id] = report
    return report


def test_large_report_is_split_without_losing_items():
    blocker = 'Chờ phía khách hàng xác nhận yêu cầu chi tiết và tài liệu đặc tả trước khi triển khai'
    items = [_item(n, vuong_mac=blocker) for n in range(1, 151)]
    items += [_item(n, project='Khác', vuong_mac=blocker) for n in range(151, 231)]
    report = _report({'OSINT3': items[:150], 'Khác': items[150:]})
    parts = reports.render_parts(report)
    assert len(parts) > 1, '230 items do not fit one part'
    covered = [item.ref for _, part_items in parts for item in part_items]
    assert sorted(covered) == sorted(i.ref for i in items) and len(covered) == len(set(covered))
    for text, part_items in parts:
        assert len(text) <= reports.PART_CHARS + 2_000
        for item in part_items:
            assert f'- {item.ref} | {item.ten}' in text
    assert 'Thống kê theo người' in parts[-1][0]


def test_coverage_note_lists_only_unmentioned_items():
    items = [
        _item(1, ten='API Login và xác thực token'),
        _item(2, ten='Layout tổng thể, UI skeleton và menu động'),
        _item(3, ten='Màn hình Login, Quên mật khẩu'),
        _item(4, ten='Đã hủy', archived='huy'),
    ]
    reports.expect_coverage('msg-1', items)
    answer = (
        'Tiến độ: **API Login và xác thực token** đạt 80%. '
        'Layout tổng thể UI skeleton và menu động đang làm (Triển).'  # paraphrased punctuation still counts
    )
    note = reports.coverage_note('msg-1', answer)
    assert 'Màn hình Login, Quên mật khẩu' in note
    assert 'API Login' not in note and 'Layout' not in note and 'Đã hủy' not in note
    assert reports.coverage_note('msg-1', answer) == '', 'checked once'

    reports.expect_coverage('msg-2', items[:1])
    reports.waive_coverage('msg-2')
    assert reports.coverage_note('msg-2', '') == '', 'an exported file carries the full list'


def test_person_stats_for_performance():
    items = [
        _item(1, phu_trach=['Long'], trang_thai='hoan_thanh', han='2026-11-10', hoan_thanh_luc='2026-11-09'),
        _item(2, phu_trach=['Long'], trang_thai='hoan_thanh', han='2026-11-10', hoan_thanh_luc='2026-11-11'),
        _item(3, phu_trach=['Long'], han='2026-11-01', tien_do=40),
        _item(4, phu_trach=['Long'], trang_thai='bi_chan', tien_do=20),
        _item(5, phu_trach=['Long'], archived='huy'),
    ]
    s = reports.person_stats(items, TODAY)['Long']
    assert (s['tong'], s['xong'], s['xong_dung_han'], s['xong_tre_han']) == (5, 2, 1, 1)
    assert (s['dang_mo'], s['qua_han'], s['bi_chan'], s['da_huy']) == (2, 1, 1, 1)
    assert s['tien_do_tb_viec_dang_mo'] == 30


@pytest.mark.parametrize('file_format', ['docx', 'xlsx', 'pdf', 'md'])
def test_export_contains_every_item(file_format):
    items = [_item(n, ten=f'Việc tiếng Việt có dấu số {n}') for n in range(1, 41)]
    report = _report({'OSINT3': items})
    name, data, content_type = build_file(report, '## Nhận xét\n- **Rủi ro**: thiếu tài liệu', file_format)
    assert name.endswith(f'.{file_format}') and data
    if file_format == 'docx':
        from docx import Document

        document = Document(io.BytesIO(data))
        text = '\n'.join(c.text for t in document.tables for row in t.rows for c in row.cells)
        assert all(item.ten in text for item in items)
    elif file_format == 'xlsx':
        from openpyxl import load_workbook

        sheet = load_workbook(io.BytesIO(data))['Hạng mục']
        assert sheet.max_row == len(items) + 1
    elif file_format == 'pdf':
        assert data.startswith(b'%PDF') and len(data) > 5_000
    else:
        assert all(item.ten in data.decode('utf-8') for item in items)


@pytest.mark.asyncio(loop_scope='module')
async def test_build_project_report_tool_end_to_end(monkeypatch):
    from open_webui.models.channels import Channels, CreateChannelForm
    from open_webui.models.messages import MessageForm, Messages
    from open_webui.models.users import Users
    from open_webui.tracker import llm, service
    from open_webui.tracker.models import Trackers
    from open_webui.tracker.schema import StateDelta
    from open_webui.tracker.tools import build_project_report

    user = await Users.insert_new_user(str(uuid.uuid4()), 'Long', f'{uuid.uuid4().hex}@test.local', role='admin')
    channel = await Channels.insert_new_channel(CreateChannelForm(name=f'rp-{uuid.uuid4().hex[:6]}'), user.id)
    await Messages.insert_new_message(MessageForm(content='Giao 60 việc'), channel.id, user.id)
    await Trackers.mark_dirty(channel.id)
    time.sleep(0.002)

    async def fake(messages):
        return StateDelta.model_validate(
            {'hang_muc_moi': [{'ten': f'Đầu việc {n}', 'phu_trach': ['Long']} for n in range(60)], 'tom_tat_lo': 's'}
        )

    monkeypatch.setattr(llm, 'generate_state_update', fake)
    claimed = [c for c in await Trackers.claim_due(time.time_ns(), 1000, 0, 60 * 10**9, 100) if c[0] == channel.id]
    await service.process_channel(*claimed[0])

    text = await build_project_report(project=channel.name, __user__={'id': user.id}, __message_id__='m-e2e')
    assert all(f'Đầu việc {n} |' in text for n in range(60)), 'every open item is in the data'
    assert 'HƯỚNG DẪN VIẾT' in text and 'Long: 60 việc' in text
    note = reports.coverage_note('m-e2e', 'Đầu việc 0, Đầu việc 1 đang chạy.')
    assert note.count('\n- Đầu việc') == 58
