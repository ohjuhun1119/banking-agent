"""5일차 도구 선택 확인용 질문 25개. 오늘 날짜는 TODAY로 고정해 채점이 날짜에 따라 바뀌지 않게 한다.

expect
  tool        기대하는 도구 ("search_terms" | "calc_interest" | None=도구를 부르지 않아야 함)
  args        calc_interest에 정확히 들어가야 하는 값 (strict=True일 때)
  allowed     strict=False일 때 넣어도 되는 값. 여기 없는 인자를 넣으면 지어낸 값(extra_arg)으로 센다
  strict      True: 이 도구를 반드시 이 값으로 불러야 통과
              False(값 부족 질문): 안 부르고 되물어도 통과, 아는 값만 넣어 불러도 통과
'150일 됐어요' · '5개월 전'에서 날짜를 계산해 넣는 것도 지어낸 값으로 센다 (정확한 날짜를 되물어야 한다).
"""
TODAY = "2026-10-28"
P = 10_000_000


def _calc(**args):
    return {"tool": "calc_interest", "args": args, "strict": True}


def _partial(**allowed):
    return {"tool": "calc_interest", "allowed": allowed, "strict": False}


SEARCH = {"tool": "search_terms", "strict": True}
NONE = {"tool": None, "strict": True}

CASES = [
    # 약관 질문 8: 검색 도구를 불러야 한다 (계산 도구를 부르면 실패)
    {"id": "T1", "group": "약관", "question": "정기예금 중도해지하면 이율이 어떻게 돼요?", "expect": SEARCH},
    {"id": "T2", "group": "약관", "question": "만기가 지나서 찾으면 이자는 어떻게 줘요?", "expect": SEARCH},
    {"id": "T3", "group": "약관", "question": "예금 일부만 먼저 해지할 수 있나요?", "expect": SEARCH},
    {"id": "T4", "group": "약관", "question": "만기 때 자동으로 재예치되나요?", "expect": SEARCH},
    {"id": "T5", "group": "약관", "question": "이 예금은 예금자보호가 되나요?", "expect": SEARCH},
    {"id": "T6", "group": "약관", "question": "비과세종합저축으로 가입할 수 있어요?", "expect": SEARCH},
    {"id": "T7", "group": "약관", "question": "중도해지금리 차등율이 뭐예요?", "expect": SEARCH},
    {"id": "T8", "group": "약관", "question": "만기일이 공휴일이면 해지는 어떻게 되나요?", "expect": SEARCH},
    # 계산 질문 8: 값이 충분하다. 계산 도구를 정확한 값으로 불러야 한다 (검색을 함께 부르는 것은 허용)
    {"id": "C1", "group": "계산", "question": "1000만원을 2026-01-01에 넣었고 만기는 2027-01-01이에요. 2026-01-21에 해지하면 이자 얼마예요?",
     "expect": _calc(principal=P, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-01-21")},
    {"id": "C2", "group": "계산", "question": "2026년 1월 1일에 천만 원을 1년 만기로 넣었는데 2026년 3월 2일에 해지하면 이자가 얼마죠?",
     "expect": _calc(principal=P, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-03-02")},
    {"id": "C3", "group": "계산", "question": "원금 10,000,000원, 신규 2026-01-01, 만기 2027-01-01, 해지 2026-05-31. 중도해지 이자 계산해 주세요.",
     "expect": _calc(principal=P, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-05-31")},
    {"id": "C4", "group": "계산", "question": "1000만원, 2026-01-01 신규, 2027-01-01 만기, 기본금리 3.5%였고 2026-07-20에 해지하면 이자는요?",
     "expect": _calc(principal=P, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-07-20", base_rate=3.5)},
    {"id": "C5", "group": "계산", "question": "2년 만기(2026-01-01 ~ 2028-01-01)로 1000만원 넣었고 기본금리는 4%예요. 2026-10-28에 깨면 얼마 받아요?",
     "expect": _calc(principal=P, start_date="2026-01-01", maturity_date="2028-01-01", termination_date="2026-10-28", base_rate=4)},
    {"id": "C6", "group": "계산", "question": "기본금리 연 3.5%, 원금 1천만원, 2026-01-01부터 2027-01-01까지인데 2026-12-07에 해지하면 이자 얼마예요?",
     "expect": _calc(principal=P, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-12-07", base_rate=3.5)},
    {"id": "C7", "group": "계산", "question": "5년짜리(2026-01-01 신규, 2031-01-01 만기) 1000만원, 기본금리 2.0%인데 2026-07-20에 해지하면 이자는?",
     "expect": _calc(principal=P, start_date="2026-01-01", maturity_date="2031-01-01", termination_date="2026-07-20", base_rate=2.0)},
    {"id": "C8", "group": "계산", "question": "2000만원을 2026-03-15에 1년 만기(2027-03-15)로 넣었어요. 오늘 해지하면 이자 얼마예요?",
     "expect": _calc(principal=20_000_000, start_date="2026-03-15", maturity_date="2027-03-15", termination_date=TODAY)},
    # 값 부족 5: 되물어도 되고, 아는 값만 넣어 불러도 된다. 질문에 없는 값을 넣으면 실패
    {"id": "M1", "group": "부족", "question": "예금 중도해지하면 이자 얼마 받아요?", "expect": _partial()},
    {"id": "M2", "group": "부족", "question": "1000만원 넣었는데 지금 깨면 이자 얼마예요?", "expect": _partial(principal=P, termination_date=TODAY)},
    {"id": "M3", "group": "부족", "question": "가입한 지 150일 됐어요. 원금은 500만원인데 오늘 해지하면 이자 얼마예요?",
     "expect": _partial(principal=5_000_000, termination_date=TODAY)},
    {"id": "M4", "group": "부족", "question": "5개월 전에 2000만원을 1년짜리로 가입했어요. 지금 해지하면 이자는요?",
     "expect": _partial(principal=20_000_000, termination_date=TODAY)},
    {"id": "M5", "group": "부족", "question": "2026-02-01에 신규했고 2026-09-30에 해지하려고 해요. 이자 얼마 나와요?",
     "expect": _partial(start_date="2026-02-01", termination_date="2026-09-30")},
    # 엣지 1: 최소 가입금액 미만. 값을 그대로 넘겨 도구가 '최소 가입금액은 100만원입니다.'를 돌려주게 해야 한다
    {"id": "E1", "group": "엣지", "question": "10만원을 2026-01-01부터 2027-01-01까지 넣었다가 2026-05-31에 해지하면 이자 얼마예요?",
     "expect": _calc(principal=100_000, start_date="2026-01-01", maturity_date="2027-01-01", termination_date="2026-05-31")},
    # 도구 불필요 3: 아무 도구도 부르지 않아야 한다
    {"id": "N1", "group": "불필요", "question": "안녕하세요", "expect": NONE},
    {"id": "N2", "group": "불필요", "question": "고마워요, 잘 알겠어요", "expect": NONE},
    {"id": "N3", "group": "불필요", "question": "오늘 서울 날씨 어때요?", "expect": NONE},
]
