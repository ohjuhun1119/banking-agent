"""날짜 계산 도구. '몇 개월이 지났는가' 판정을 이 파일의 함수 하나로 모아 두어서, 은행 확인으로 규칙이 바뀌면 여기만 고친다."""
import calendar
from datetime import date


def add_months(d: date, n: int) -> date:
    """d에 n개월을 더한 날. 그 달에 같은 날짜가 없으면 말일로 보정한다 (예: 1월 31일 + 1개월 = 2월 28일)."""
    year_offset, month_index = divmod(d.month - 1 + n, 12)
    year, month = d.year + year_offset, month_index + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def completed_months(start: date, end: date) -> int:
    """신규일(start)부터 end까지 완전히 지난 개월 수.

    [가정, 은행 확인 전] start에 n개월을 더한 날이 end 이하인 가장 큰 n. 문서에 '완전히 지나간 기간'이라는 정의만 있고
    월말 신규일(29~31일)의 처리 규칙은 없어서, 만기일 계산과 같은 방식(해당 월 말일로 보정)을 쓴다.
    """
    if end < start:
        return 0
    n = (end.year - start.year) * 12 + (end.month - start.month)
    if add_months(start, n) > end:
        n -= 1
    return max(n, 0)
