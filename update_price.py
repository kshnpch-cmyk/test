import os
import json
import time
import re
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def fetch_naver_price(search_query):
    """
    네이버 쇼핑 페이지의 __NEXT_DATA__ 파싱을 통해 
    실제 최저가(K열), 판매채널(J열), 택배비(L열)를 추출합니다.
    """
    print(f"  [조사 요청] 검색어: '{search_query}'")
    
    # 봇 감지 우회용 헤더 설정
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1"
    }
    
    encoded_query = requests.utils.quote(search_query)
    url = f"https://search.shopping.naver.com/search/all?where=all&frm=NVSCTAB&query={encoded_query}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        print(f"  [응답 상태] {response.status_code}")
        
        if response.status_code == 200:
            html = response.text
            
            # __NEXT_DATA__ 스크립트 내부의 JSON 데이터 파싱
            pattern = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
            match = re.search(pattern, html, re.DOTALL)
            
            if match:
                json_str = match.group(1)
                data = json.loads(json_str)
                
                # 검색 결과 목록 접근
                products = data.get('props', {}).get('pageProps', {}).get('initialResult', {}).get('shoppingResult', {}).get('products', [])
                
                if products:
                    first = products[0]
                    
                    # 1. J열: 판매채널
                    channel = first.get('mallName') or "네이버쇼핑"
                    
                    # 2. K열: 판매가
                    price_val = first.get('price') or first.get('lprice') or 0
                    price = int(price_val)
                    
                    # 3. L열: 택배비
                    delivery_fee = first.get('deliveryFee', 0)
                    delivery_content = str(first.get('deliveryFeeContent', ''))
                    
                    if delivery_fee == 0 or "무료" in delivery_content:
                        shipping = "무료배송"
                    else:
                        shipping = f"{delivery_fee}원" if isinstance(delivery_fee, int) else delivery_content

                    return channel, price, shipping

    except Exception as e:
        print(f"  ❌ 크롤링 에러: {e}")

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
        print("📊 [최저가 조사 시작] 3행부터 10행까지 조사를 진행합니다.")
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
            channel, price, shipping = fetch_naver_price(search_query)

            # 2차 시도 (실패 시): C열(품목명) 단독 검색
            if not channel or price == 0:
                print(f"  └─ ⚠️ 1차 검색 실패. 품목명 단독 재검색 시도: '{product_name}'")
                channel, price, shipping = fetch_naver_price(product_name.strip())

            if not channel or price == 0:
                channel, price, shipping = "검색결과없음", 0, "-"

            # J열(10: 판매채널), K열(11: 판매가), L열(12: 택배비) 업데이트
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping}\n")
            time.sleep(2.0)

        print("🎉 모든 상품 조사 및 시트 업데이트가 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
