"""Send the sample reminder emails (the same templates and sending account as the real reminders) to test addresses.

Run inside the open-webui container, so the saved email settings (Microsoft 365 or SMTP) are used:

    docker exec open-webui python -m open_webui.tracker.mail_test --to minhquan130599@gmail.com
    docker exec open-webui python -m open_webui.tracker.mail_test --to a@gmail.com,b@ncsgroup.vn --kind owner
    docker exec open-webui python -m open_webui.tracker.mail_test --save /tmp/mail-preview      (write HTML, no email)

--kind: assignee (email to the person in charge), owner (daily digest to the channel owner) or all (default).
"""

import argparse
import asyncio
import os
import sys
import time


async def main(args) -> int:
    from open_webui.models.config import Config
    from open_webui.tracker import mailer
    from open_webui.tracker.mywork import today

    settings = await mailer.settings()
    day = today()
    base = ((await Config.get('webui.url')) or os.getenv('OPEN_WEBUI_PUBLIC_URL', '') or '').rstrip('/')
    kinds = ['assignee', 'owner'] if args.kind == 'all' else [args.kind]

    def build(kind: str, name: str) -> tuple[str, str]:
        if kind == 'owner':
            channels = mailer.sample_owner_digest(day)
            subject = '[Email thử] ' + mailer.owner_subject(channels, day)
            body = mailer.owner_digest_html(name, channels, f'{base}/my-work?mode=team' if base else '', '', day)
        else:
            items = mailer.sample_reminders(day)
            subject = '[Email thử] ' + mailer.reminder_subject(items, day)
            body = mailer.reminder_html(name, items, f'{base}/my-work' if base else '', '', day)
        return subject, body

    if args.save:
        os.makedirs(args.save, exist_ok=True)
        for kind in kinds:
            subject, body = build(kind, args.name or 'Quân')
            path = os.path.join(args.save, f'email-{kind}.html')
            with open(path, 'w', encoding='utf-8') as f:
                f.write(body)
            print(f'Đã lưu {path}  (tiêu đề: {subject})')
        return 0

    recipients = [a.strip() for a in (args.to or '').replace(';', ',').split(',') if a.strip()]
    if not recipients or any('@' not in a for a in recipients):
        print('Cần --to <địa chỉ email> (nhiều địa chỉ cách nhau bằng dấu phẩy)')
        return 2
    missing = mailer.missing(settings)
    if missing:
        print('Cấu hình gửi email còn thiếu: ' + ', '.join(missing) + ' (trang giám sát → Chỉnh sửa cấu hình → Email)')
        return 2
    how = (
        f'SMTP {settings["smtp_host"]}:{settings["smtp_port"]}' if settings['method'] == 'smtp' else 'Microsoft 365 (Graph)'
    )
    print(f'Gửi bằng {how}, từ {settings.get("sender")}')

    failed = 0
    for to in recipients:
        for kind in kinds:
            subject, body = build(kind, args.name or to.split('@')[0])
            started = time.perf_counter()
            try:
                await mailer.send(to, subject, body, settings)
                print(f'  OK   {to:<36} {kind:<9} {round((time.perf_counter() - started) * 1000)} ms  {subject}')
            except Exception as error:
                failed += 1
                print(f'  LỖI  {to:<36} {kind:<9} {error}')
    if not failed:
        print('Xong. Gmail: xem cả mục Spam / Quảng cáo nếu không thấy trong Hộp thư đến.')
    return 1 if failed else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Gửi email nhắc việc mẫu để kiểm tra hiển thị')
    parser.add_argument('--to', help='địa chỉ nhận, nhiều địa chỉ cách nhau bằng dấu phẩy')
    parser.add_argument('--kind', choices=['assignee', 'owner', 'all'], default='all')
    parser.add_argument('--name', help='tên trong lời chào (mặc định: phần trước @)')
    parser.add_argument('--save', help='chỉ lưu HTML vào thư mục này, không gửi')
    sys.exit(asyncio.run(main(parser.parse_args())))
