import os
import json
import time
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def fetch_naver_mobile_price(search_query):
    """
    네이버 모바일 쇼핑 검색을 조회하여 차단(418)을 우회하고
    J(채널), K(가격), L(택배비) 정보를 수집합니다.
    """
    print(f"  [조사 요청] 키워드: '{search_query}'")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://msearch.shopping.naver.com/"
    }
    
    encoded_query = requests.utils.quote(search_query)
    url = f"https://msearch.shopping.naver.com/search/all?query={encoded_query}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"  [응답 코드] {response.status_code}")
        
        if response.status_code == 200:
            html_text = response.text
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html_text, 'html.parser')
            
            # 모바일 쇼핑 검색 결과 상품 카드 추출
            product = soup.select_one("div[class*='product_item']") or soup.select_one("li[class*='product_item']") or soup.select_one("div[class*='list_search']")
            
            if product:
                # 1. J열: 판매채널
                channel_el = product.select_one("[class*='mall']") or product.select_one("[class*='seller']")
                channel_name = channel_el.text.strip() if channel_el else "네이버쇼핑"
                
                # 2. K열: 판매가
                price_el = product.select_one("[class*='price']")
                if price_el:
                    import re
                    price_nums = re.findall(r'\d+', price_el.text.replace(",", ""))
                    sale_price = int("".join(price_nums)) if price_nums else 0
                else:
                    sale_price = 0
                
                # 3. L열: 택배비
                delivery_el = product.select_one("[class*='delivery']") or product.select_one("[class*='ship']")
                if delivery_el:
                    delivery_text = delivery_el.text.strip()
                    shipping_fee = "무료배송" if "무료" in delivery_text else delivery_text
                else:
                    shipping_fee = "기본배송"

                return channel_name, sale_price, shipping_fee

    except Exception as e:
        print(f"  ❌ 크롤링 예외: {e}")

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

        print("==================================================")
        print("📊 [실제 수집 가동] 3행부터 10행까지 처리합니다.")
        print("==================================================\n")

        for row_num in range(3, 11):
            row_values = worksheet.row_values(row_num)
            
            # 정확한 시트 열 매핑
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열 (품목명)
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열 (규격)
            category = row_values[8] if len(row_values) >= 9 else ""      # I열 (구분: 전용, 유사, 동일 등)
            note = row_values[13] if len(row_values) >= 14 else ""        # N열 (특이사항)

            print(f"▶ [{row_num}행] 품목: '{product_name}' | 규격: '{spec}' | 구분(I열): '{category}'")

            # 스킵 조건: I열(구분)에 '전용', '예산', '종료' 포함 시 수집 제외
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨 (구분 조건 제외: '{category}')\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨 (품목명 없음)\n")
                continue

            # 1차 시도: C열(품목명) + D열(규격)
            search_query = f"{product_name} {spec}".strip()
            channel, price, shipping = fetch_naver_mobile_price(search_query)

            # 2차 시도 (실패 시): C열(품목명) 단독
            if not channel:
                print(f"  └─ ⚠️ 1차 실패, C열 단독 재검색 시도: '{product_name}'")
                channel, price, shipping = fetch_naver_mobile_price(product_name.strip())

            if not channel:
                channel, price, shipping = "검색결과없음", 0, "-"

            # J열(10: 판매채널), K열(11: 판매가), L열(12: 택배비) 업데이트
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            print(f"  ✔ [완료] J={channel} | K={price}원 | L={shipping}\n")
            time.sleep(2.0)

        print("🎉 수집 작업이 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
