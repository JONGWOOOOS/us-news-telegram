"""네이버 뉴스 키워드 모니터링 → 텔레그램 전송 (GitHub Actions용)"""
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

NAVER_ID = os.environ["NAVER_CLIENT_ID"]
NAVER_SECRET = os.environ["NAVER_CLIENT_SECRET"]
TG_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TG_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

KEYWORDS_FILE = "keywords.txt"
SENT_FILE = "sent_links.json"
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "3"))  # 이 시간보다 오래된 기사는 무시
MAX_SENT_KEEP = 3000                                         # 중복 체크용으로 보관할 링크 수
KST = timezone(timedelta(hours=9))


def load_keywords():
    with open(KEYWORDS_FILE, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.startswith("#")]


def load_sent():
    if not os.path.exists(SENT_FILE):
        return []
    with open(SENT_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_sent(sent):
    with open(SENT_FILE, "w", encoding="utf-8") as f:
        json.dump(sent[-MAX_SENT_KEEP:], f, ensure_ascii=False, indent=0)


def clean(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def search_news(query):
    url = "https://openapi.naver.com/v1/search/news.json?" + urllib.parse.urlencode(
        {"query": query, "display": 50, "sort": "date"}
    )
    req = urllib.request.Request(url, headers={
        "X-Naver-Client-Id": NAVER_ID,
        "X-Naver-Client-Secret": NAVER_SECRET,
    })
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r).get("items", [])


def send_telegram(text):
    data = urllib.parse.urlencode({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=20) as r:
        r.read()
    time.sleep(1)  # 텔레그램 전송 속도 제한 대비


def send_chunked(header, lines):
    """텔레그램 한 메시지 4096자 제한에 맞춰 나눠 보냄"""
    msg = header
    for line in lines:
        if len(msg) + len(line) + 2 > 3900:
            send_telegram(msg)
            msg = header + " (계속)"
        msg += "\n\n" + line
    send_telegram(msg)


def main():
    keywords = load_keywords()
    sent = load_sent()
    sent_set = set(sent)
    cutoff = datetime.now(KST) - timedelta(hours=MAX_AGE_HOURS)
    total = 0
    errors = []

    for kw in keywords:
        try:
            items = search_news(kw)
        except Exception as e:
            print(f"[오류] '{kw}' 검색 실패: {e}", file=sys.stderr)
            errors.append(f"'{kw}' 검색 실패: {e}")
            continue

        lines = []
        new_links = []
        for it in items:
            link = it.get("originallink") or it["link"]
            naver_link = it["link"]
            if link in sent_set or naver_link in sent_set:
                continue
            pub = parsedate_to_datetime(it["pubDate"])
            if pub < cutoff:
                continue
            title = html.escape(clean(it["title"]))
            desc = html.escape(clean(it["description"]))[:120]
            # 네이버 뉴스 링크(n.news.naver.com)가 있으면 그걸 우선 사용
            best = html.escape(naver_link if "news.naver.com" in naver_link else link, quote=True)
            lines.append(
                f"• <a href=\"{best}\">{title}</a>\n"
                f"  <i>{pub.astimezone(KST):%m/%d %H:%M}</i> · {desc}…"
            )
            new_links.append(link)
            sent_set.add(link)

        if lines:
            lines.reverse()  # 오래된 기사부터
            try:
                send_chunked(f"📰 <b>[{html.escape(kw)}]</b> 새 기사 {len(lines)}건", lines)
            except Exception as e:
                print(f"[오류] '{kw}' 텔레그램 전송 실패: {e}", file=sys.stderr)
                errors.append(f"'{kw}' 텔레그램 전송 실패: {e}")
                continue  # 전송 실패한 기사는 기록하지 않아 다음 실행 때 다시 시도
            sent.extend(new_links)
            total += len(lines)

    save_sent(sent)
    print(f"완료: 새 기사 {total}건 전송")
    if errors:
        # 실패로 끝내서 GitHub 실패 메일 + 텔레그램 오류 알림이 가도록 함
        print(f"오류 {len(errors)}건 발생", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
