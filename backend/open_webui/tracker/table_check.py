"""Read spreadsheets the way the tracker does and print the tasks, to review a change on real files before it
reaches the channels. Uses the tracker's model connection; writes no task, only the usual call metrics.

    docker cp ./files open-webui:/tmp/files
    docker exec -it open-webui python -m open_webui.tracker.table_check /tmp/files/*.xlsx
    docker exec -it open-webui python -m open_webui.tracker.table_check /tmp/files/*.xlsx --json /tmp/ket_qua.json
"""

import argparse
import asyncio
import datetime as dt
import json
import logging
import os

from open_webui.tracker.service import TZ


def _line(item: dict) -> str:
    owners = ', '.join(item.get('phu_trach') or []) or 'CHƯA GIAO'
    progress = f" {item['tien_do']}%" if item.get('tien_do') is not None else ''
    key = (item.get('khoa') or '').split(':', 2)[-1]
    return f"  {key[:22]:<22} | {owners[:28]:<28} | {item.get('han') or '-':<10} | {item.get('trang_thai')}{progress} | {item['ten']}"


async def check(paths: list[str], out: str = '') -> None:
    from open_webui.file_pipeline.tables import read_grid
    from open_webui.tracker import extract

    today = dt.datetime.now(TZ).date().isoformat()
    results = []
    for path in paths:
        name = os.path.basename(path)
        sheets = await asyncio.to_thread(read_grid, path, name, True)
        started = dt.datetime.now()
        result = await extract.extract_table(name, sheets, today)
        seconds = (dt.datetime.now() - started).total_seconds()
        report = result['report']
        split = report.get('split') or {}
        print(f'\n===== {name}  ({seconds:.0f}s, cách đọc: {report.get("mode")})')
        if report.get('sheets_skipped'):
            print('  sheet không đọc:', report['sheets_skipped'])
        if report.get('unread_rows'):
            print('  dòng chưa đọc được:', ', '.join(report['unread_rows']))
        if report.get('issues'):
            print('  vấn đề:', '; '.join(report['issues']))
        print('  ' + ', '.join(f'{k}={v}' for k, v in split.items() if v and k not in ('not_task_rows', 'unread_rows', 'sheets_skipped')))
        for row in split.get('not_task_rows') or []:
            print(f"  (bỏ dòng {row['dong']}: {row['noi_dung']})")
        for item in result['items']:
            print(_line(item))
            for detail in item.get('chi_tiet') or []:
                print(f'      · {detail}')
            if item.get('vuong_mac'):
                print(f"      ! {item['vuong_mac']}")
        results.append({'file': name, 'seconds': round(seconds), 'report': report, 'items': result['items']})
    if out:
        with open(out, 'w', encoding='utf-8') as handle:
            json.dump(results, handle, ensure_ascii=False, indent=1, default=str)
        print(f'\nĐã ghi {out}')


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description='Đọc thử file bảng tính như tracker và in các đầu việc.')
    parser.add_argument('files', nargs='+')
    parser.add_argument('--json', default='', help='ghi toàn bộ kết quả ra file JSON')
    args = parser.parse_args()
    asyncio.run(check(args.files, args.json))
