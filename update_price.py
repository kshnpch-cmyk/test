import os
import json
import time
import re
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup

def get_free_proxies():
    """공개 무료 프록시 리스트를 수집합니다."""
    url = "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt"
    try:
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            proxies = res.text.strip().split('\n')
            return [p.strip() for p in proxies if p.strip()]
    except Exception:
        pass
    return []

def fetch_naver_price_proxy(search_query, proxy_list):
    """
    프록시 우회를 통해 네이버 쇼핑에서
    판매채널(J열), 최저가(K열), 택배비(L열)를 가져옵니다.
    """
    clean_query = search_query.replace("*", " ").strip()
    print(f"  [조사 요청] 검색어: '{clean_query}'")
    
    encoded_query = requests.utils.quote(clean_query)
    url = f"https://msearch.shopping.naver.com/search/all?query={encoded_query}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://msearch.shopping.naver.com/"
    }

    # 프록시 시도 (최대 5개 프록시 번갈아 테스트)
    for i, proxy_ip in enumerate(proxy_list[:5]):
        proxies = {
            "http": f"http://{proxy_ip}",
            "https": f"http://{proxy_ip}"
        }
        try:
            print(f"  [프록시 시도 {i+1}/5] {proxy_ip}")
            response = requests.get(url, headers=headers, proxies=proxies, timeout=5)
            
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                text_content = soup.get_text()
                
                # 1. 가격 추출
                price_matches = re.findall(r'([\d,]+)\s*원', text_content)
                valid_prices = []
                for p in price_matches:
                    num = int(p.replace(",", ""))
                    if num >= 100:
                        valid_prices.append(num)
                
                sale_price = min(valid_prices) if valid_prices else 0

                if sale_price > 0:
                    # 2. 판매채널
                    channel_name = "네이버쇼핑"
                    mall_el = soup.select_one("[class*='mall']") or soup.select_one("[class*='seller']")
                    if mall_el and mall_el.text.strip():
                        channel_name = mall_el.text.strip()

                    # 3. 배송비
                    shipping_fee = "무료배송" if "무료" in text_content else "기본배송"

                    print(f"  ✅ 프록시 접속 성공! ({proxy_ip})")
                    return channel_name, sale_price, shipping_fee

        except Exception:
            continue

    # 프록시 연결 전체 실패 시 일반 요청 fallback
    try:
        print("  ⚠️ 프록시 접속 실패로 직접 요청을 시도합니다.")
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            text_content = soup.get_text()
            price_matches = re.findall(r'([\d,]+)\s*원', text_content)
            valid_prices = [int(p.replace(",", "")) for p in price_matches if int(p.replace(",", "")) >= 100]
            sale_price = min(valid_prices) if valid_prices else 0
            if sale_price > 0:
                return "네이버쇼핑", sale_price, "기본배송"
    except Exception:
        pass

    return None, None, None

def run_price_update():
    try:
        # 1. 구글 인증
        token_secret = os.environ.get('GCP_TOKEN_JSON')
        if not token_secret:
            raise ValueError("❌ GitHub Secret에 'GCP_TOKEN_JSON'이 설정되지 않았습니다.")
        
        token_info = json.loads(token_secret)
        token_info.pop("scopes", None)
        token_info.pop("scope", None)

        credentials = Credentials.from_authorized_user_info(token_info)
        credentials._scopes = None

        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())

        gc = gspread.authorize(credentials)

        # 2. 구글 시트 연결
        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        doc = gc.open_by_url(sheet_url)
        worksheet = doc.get_worksheet(0)

        # 3. 우회용 프록시 목록 확보
        print("🌐 우회용 프록시 서버 목록을 불러오는 중...")
        proxy_list = get_free_proxies()
        print(f"  └─ 확보된 프록시 수: {len(proxy_list)}개")

        print("==================================================")
        print("📊 [프록시 우회 가동] 3행부터 10행까지 조사를 시작합니다.")
        print("==================================================\n")

        for row_num in range(3, 11):
            row_values = worksheet.row_values(row_num)
            
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열 (품목명)
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열 (규격)
            category = row_values[8] if len(row_values) >= 9 else ""      # I열 (구분)

            print(f"▶ [{row_num}행] 품목: '{product_name}' | 규격: '{spec}' | 구분(I열): '{category}'")

            # 스킵 조건: I열(구분)에 '전용', '예산', '종료'가 포함된 경우
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨 (구분 조건 제외: '{category}')\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨 (품목명 없음)\n")
                continue

            # 1차 시도: C열(품목명) + D열(규격)
            search_query = f"{product_name} {spec}".strip()
            channel, price, shipping = fetch_naver_price_proxy(search_query, proxy_list)

            # 2차 시도: C열(품목명) 단독
            if not channel or price == 0:
                print(f"  └─ ⚠️ 1차 실패 후 품목명 단독 재검색 시도: '{product_name}'")
                channel, price, shipping = fetch_naver_price_proxy(product_name.strip(), proxy_list)

            if not channel or price == 0:
                channel, price, shipping = "검색결과없음", 0, "-"

            # J열(10: 판매채널), K열(11: 판매가), L열(12: 택배비) 업데이트
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping}\n")
            time.sleep(1.5)

        print("🎉 해외 IP 우회 적용 시트 업데이트 완료!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
