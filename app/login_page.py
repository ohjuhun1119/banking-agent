"""접근 키 로그인 화면. 은행 앱 로그인처럼 장식 없이: 흰 바탕, 글자, 파란 버튼 하나, 네모진 입력칸.

자기 완결형 HTML이다: 로그인 전에는 /static이 접근 키로 막혀 있어 CSS · 글꼴 · 이미지를 따로 불러올 수 없다 (그래서 인라인).
화면 문구에 'AI · 모델 · 도구' 같은 내부 용어를 쓰지 않는다 (tests/test_login_page.py가 확인).
"""
from __future__ import annotations

SECURITY_HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY"}         # 키 입력 화면은 캐시하지 않고 다른 사이트의 프레임에 넣지 못하게 한다

_STYLE = """*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",system-ui,sans-serif;color:#1a1a1a;background:#fff}
main{max-width:420px;min-height:100vh;margin:0 auto;padding:56px 24px 32px;display:flex;flex-direction:column}
.brand{font-size:15px;font-weight:700;color:#1f4e9c;letter-spacing:-0.2px}
h1{font-size:24px;line-height:1.4;font-weight:700;letter-spacing:-0.5px;margin:40px 0 8px}
.lead{font-size:14px;line-height:1.6;color:#6b7280;margin:0 0 36px}
label{display:block;font-size:13px;color:#4b5563;margin-bottom:8px}
input{width:100%;height:52px;border:1px solid #d1d5db;border-radius:4px;padding:0 14px;font-size:16px;font-family:inherit;color:#1a1a1a;background:#fff}
input:focus{outline:1px solid #1f4e9c;border-color:#1f4e9c}
input[aria-invalid="true"]{border-color:#d92d20;outline-color:#d92d20}
.err{font-size:13px;color:#d92d20;margin:8px 0 0}
.hint{font-size:12px;color:#9ca3af;margin:8px 0 0}
button{width:100%;height:52px;margin-top:28px;border:0;border-radius:4px;background:#1f4e9c;color:#fff;font-size:16px;font-weight:700;font-family:inherit;cursor:pointer}
button:active{background:#183f7d}
footer{margin-top:auto;padding-top:48px;font-size:12px;line-height:1.7;color:#9ca3af;text-align:center}"""


def render(error: bool = False) -> str:
    field = '<input id="key" name="key" type="password" autocomplete="off" autofocus required aria-invalid="true" aria-describedby="key-error">' if error else \
            '<input id="key" name="key" type="password" autocomplete="off" autofocus required aria-describedby="key-hint">'
    note = '<p id="key-error" class="err" role="alert">접근 키가 맞지 않습니다. 다시 확인해 주세요.</p>' if error else \
           '<p id="key-hint" class="hint">담당자에게 받은 접근 키를 입력하세요.</p>'
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>정기예금 상담</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
<div class="brand">정기예금 상담</div>
<h1>접근 키를 입력해 주세요</h1>
<p class="lead">약관 확인과 중도해지 이자 계산을 안내해 드려요.</p>
<form method="post" action="/login">
<label for="key">접근 키</label>
{field}
{note}
<button type="submit">로그인</button>
</form>
<footer>실제 금융 상담이 아니며 참고용입니다.<br>실제 이자는 은행의 해지예상조회로 확인해 주세요.</footer>
</main>
</body>
</html>
"""
