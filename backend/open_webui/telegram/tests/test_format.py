"""AI answers as Telegram HTML (pure functions, no database)."""

from open_webui.telegram import format as fmt


def test_reasoning_tool_calls_and_citations_are_removed():
    text = fmt.clean('<details type="reasoning">nghĩ</details>Kết quả [1] đây [2, 3].<think>x</think>')
    assert text == 'Kết quả đây.'


def test_links_that_only_open_on_the_web_are_removed():
    answer = (
        'Tài liệu hướng dẫn tra cứu [tr.1–2] và phân tích hồ sơ [tr.3](/api/v1/files/abc/pages/3/image).\n'
        'Xem [file gốc](/api/v1/files/abc/content) hoặc [trang](https://a.vn).\n'
        '![ảnh](/api/v1/files/abc/pages/1/image)\n\n'
        'Xem trang gốc (1793161106000_Phân tích (2).pdf): [tr.1](/api/v1/files/e5/pages/1/image) · '
        '[tr.2](/api/v1/files/e5/pages/2/image)'
    )
    text = fmt.clean(answer)
    assert '/api/' not in text and 'Xem trang gốc' not in text
    assert 'phân tích hồ sơ.' in text and '[tr.1–2]' in text
    assert 'Xem file gốc hoặc [trang](https://a.vn).' in text


def test_markdown_becomes_telegram_html_and_user_text_is_escaped():
    html = fmt.to_html('## Tiến độ **OPP 3**\n- Việc <b>A</b> & B\n- *nghiêng* `x<y` [web](https://a.vn/?a=1&b=2)')
    assert html.splitlines()[0] == '<b>Tiến độ OPP 3</b>'
    assert '• Việc &lt;b&gt;A&lt;/b&gt; &amp; B' in html
    assert '<i>nghiêng</i>' in html and '<code>x&lt;y</code>' in html
    assert '<a href="https://a.vn/?a=1&amp;b=2">web</a>' in html


def test_only_http_links_become_links():
    assert '<a ' not in fmt.to_html('[x](javascript:alert(1))')


def test_tables_become_lines():
    html = fmt.to_html('| Việc | Người |\n|---|---|\n| Dự toán | An |')
    assert html == '<b>Việc · Người</b>\n• Việc: Dự toán · Người: An'


def test_code_blocks_are_escaped_and_never_cut():
    code = '```\n' + '\n'.join(f'print("<{i}>")' for i in range(400)) + '\n```'
    pieces = fmt.chunks('Mở đầu\n' + code, limit=500)
    assert len(pieces) > 2
    assert all(p.count('```') % 2 == 0 for p in pieces)  # every message opens and closes its code
    assert all(len(p) <= 600 for p in pieces)
    assert '&lt;1&gt;' in fmt.to_html(code)


def test_long_answers_are_split_under_the_telegram_limit():
    messages = fmt.messages('\n'.join(f'Dòng {i} ' + 'x' * 80 for i in range(300)), header='<b>H</b>')
    assert len(messages) > 1
    assert messages[0].startswith('<b>H</b>\n')
    assert all(len(m) <= 4096 for m in messages)


def test_preview_keeps_the_end_and_closes_open_code():
    text = fmt.preview('a' * 5000 + '\n```\ncode', limit=1000)
    assert text.startswith('…') or len(text) < 1100
    assert '<pre>' in text
