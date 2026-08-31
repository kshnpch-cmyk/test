import os
import json
import time
import re
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup

def fetch_naver_price_mobile(search_query):
    """
    네이버 모바일 쇼핑 페이지에서 판매채널(J열), 최저가(K열), 택배비(L열)를 크롤링합니다.
    실패 시 디버깅을 위해 전체 텍스트를 로그에 출력합니다.
    """
    clean_query = search_query.replace("*", " ").strip()
    print(f"  [조사 요청] 검색어: '{clean_query}'")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://msearch.shopping.naver.com/"
    }
    
    encoded_query = requests.utils.quote(clean_query)
    url = f"https://msearch.shopping.naver.com/search/all?query={encoded_query}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"  [응답 코드] {response.status_code}")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            text_content = soup.get_text(strip=True)
            
            # 1. 가격 추출 (예: "30,670원" 또는 "1,800원" 형태)
            price_matches = re.findall(r'([\d,]+)\s*원', text_content)
            sale_price = 0
            
            if price_matches:
                valid_prices = []
                for p in price_matches:
                    num = int(p.replace(",", ""))
                    if num >= 100:  # 100원 이상의 실제 상품 가격만 선별
                        valid_prices.append(num)
                if valid_prices:
                    sale_price = min(valid_prices)

            # 2. 판매채널 추출
            channel_name = "네이버쇼핑"
            mall_el = soup.select_one("[class*='mall']") or soup.select_one("[class*='seller']") or soup.select_one("[class*='store']")
            if mall_el and mall_el.text.strip():
                channel_name = mall_el.text.strip()

            # 3. 배송비 추출
            shipping_fee = "기본배송"
            if "무료배송" in text_content or "무료" in text_content:
                shipping_fee = "무료배송"
            else:
                delivery_match = re.search(r'배송비\s*([\d,]+)\s*원', text_content)
                if delivery_match:
                    shipping_fee = f"{delivery_match.group(1)}원"

            if sale_price > 0:
                return channel_name, sale_price, shipping_fee
            else:
                print("  ⚠️ [추출 실패] 텍스트 파싱에서 유효한 가격을 찾지 못했습니다.")
                print(f"  📄 --- [네이버 응답 본문 전체 텍스트 (총 {len(text_content)}자)] ---")
                print(text_content[:3000])  # 본문 상위 3,000자 전체 출력
                print("  --------------------------------------------------")

    except Exception as e:
        print(f"  ❌ 크롤링 예외: {e}")

    return None, None, None

def run_price_update():
    try:
        # 1. 구글 인증 (GCP_TOKEN_JSON)
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

        print("==================================================")
        print("📊 [가격 수집 및 진단 가동] 3행부터 10행까지 조사를 시작합니다.")
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
            channel, price, shipping = fetch_naver_price_mobile(search_query)

            # 2차 시도 (실패 시): C열(품목명) 단독 검색
            if not channel or price == 0:
                print(f"  └─ ⚠️ 1차 실패 후 품목명 단독 재검색 시도: '{product_name}'")
                channel, price, shipping = fetch_naver_price_mobile(product_name.strip())

            if not channel or price == 0:
                channel, price, shipping = "검색결과없음", 0, "-"

            # J열(10: 판매채널), K열(11: 판매가), L열(12: 택배비) 업데이트
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping}\n")
            time.sleep(2.0)

        print("🎉 모든 작업 및 진단 출력이 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
