"""중도해지 이자 계산의 기준 사례 (원금 10,000,000원, 이자 = 원금 × 금리 × 경과일수 / 365, 원 미만 절사).

노션의 테스트 케이스 A~G와 같은 값이다. 시험(tests/test_interest.py)과 확인 화면(이자 계산 탭)이 이 파일 하나를 함께 쓴다.
"""
P = 10_000_000

CASES = [
    {"id": "A", "label": "1개월 미만", "principal": P, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
     "termination_date": "2026-01-21", "base_rate": None, "elapsed_days": 20, "section": "1개월 미만", "rate_pct": "0.100", "interest": 547},
    {"id": "B", "label": "1~3개월", "principal": P, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
     "termination_date": "2026-03-02", "base_rate": None, "elapsed_days": 60, "section": "1개월 이상 ~ 3개월 미만", "rate_pct": "0.150", "interest": 2465},
    {"id": "C", "label": "3~6개월", "principal": P, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
     "termination_date": "2026-05-31", "base_rate": None, "elapsed_days": 150, "section": "3개월 이상 ~ 6개월 미만", "rate_pct": "0.200", "interest": 8219},
    {"id": "D", "label": "6~9개월 (60%)", "principal": P, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
     "termination_date": "2026-07-20", "base_rate": 3.5, "elapsed_days": 200, "section": "6개월 이상 ~ 9개월 미만", "rate_pct": "1.150", "interest": 63013},
    {"id": "E", "label": "9~11개월 (70%)", "principal": P, "start_date": "2026-01-01", "maturity_date": "2028-01-01",
     "termination_date": "2026-10-28", "base_rate": 4.0, "elapsed_days": 300, "section": "9개월 이상 ~ 11개월 미만", "rate_pct": "1.150", "interest": 94520},
    {"id": "F", "label": "11개월 이상 (90%)", "principal": P, "start_date": "2026-01-01", "maturity_date": "2027-01-01",
     "termination_date": "2026-12-07", "base_rate": 3.5, "elapsed_days": 340, "section": "11개월 이상", "rate_pct": "2.934", "interest": 273304},
    {"id": "G", "label": "최저 0.20% 적용", "principal": P, "start_date": "2026-01-01", "maturity_date": "2031-01-01",
     "termination_date": "2026-07-20", "base_rate": 2.0, "elapsed_days": 200, "section": "6개월 이상 ~ 9개월 미만", "rate_pct": "0.200", "interest": 10958},
]
