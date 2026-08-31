import os
import json
import time
import re
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def extract_products_from_json(data):
    """
    네이버 __NEXT_DATA__ JSON 내부를 깊이 탐색하여 
    상품 리스트(products)를 자동으로 찾아냅니다.
    """
    try:
        # 경로 1
        products = data['props']['pageProps']['initialResult']['shoppingResult']['products']
        if products: return products
    except Exception:
        pass
        
    try:
        # 경로 2
        products = data['props']['pageProps']['compositeResult']['shoppingResult']['products']
        if products: return products
    except Exception:
        pass

    try:
        # 경로 3 (모바일/기타)
        products = data['shoppingResult']['products']
        if products: return products
    except Exception:
        pass

    return []

def fetch_naver_price(search_query):
    """
    네이버 쇼핑 페이지에서 최저가(K열), 판매채널(J열), 택배비(L열)를 파싱합니다.
    """
    print(f"  [조사 요청] 검색어: '{search_query}'")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://search.shopping.naver.com/"
    }
    
    # 규격 및 검색어 내 특수 기호 정제 (* -> 공백)
    clean_query = search_query.replace("*", " ").strip()
    encoded_query = requests.utils.quote(clean_query)
    url = f"https://search.shopping.naver.com/search/all?where=all&frm=NVSCTAB&query={encoded_query}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            html = response.text
            
            # __NEXT_DATA__ JSON 추출
            pattern = r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>'
            match = re.search(pattern, html, re.DOTALL)
            
            if match:
                json_str = match.group(1)
                data = json.loads(json_str)
                
                products = extract_products_from_json(data)
                
                if products:
                    first = products[0]
                    
                    # 1. J열: 판매채널
                    channel = first.get('mallName') or first.get('seller') or "네이버쇼핑"
                    
                    # 2. K열: 판매가
                    price_val = first.get('price') or first.get('lprice') or first.get('discountPrice') or 0
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
        print(f"  ❌ 수집 예외 발생: {e}")

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
        print("📊 [네이버 가격 수집 가동] 3행부터 10행까지 조사를 시작합니다.")
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

            # 1차 시도: 품목명 + 규격
            search_query = f"{product_name} {spec}".strip()
            channel, price, shipping = fetch_naver_price(search_query)

            # 2차 시도 (실패 시): 품목명 단독 (예: '서울우유 바리스타밀크 1L')
            if not channel or price == 0:
                print(f"  └─ ⚠️ 1차 실패 후 품목명 단독 재검색: '{product_name}'")
                channel, price, shipping = fetch_naver_price(product_name.strip())

            # 3차 시도 (실패 시): 품목명에서 브랜드/핵심어만 추출
            if not channel or price == 0:
                # 괄호나 영문 등 일부 간소화
                short_name = re.sub(r'\(.*?\)', '', product_name).strip()
                if short_name != product_name:
                    print(f"  └─ ⚠️ 2차 실패 후 품목명 간소화 재검색: '{short_name}'")
                    channel, price, shipping = fetch_naver_price(short_name)

            if not channel or price == 0:
                channel, price, shipping = "검색결과없음", 0, "-"

            # J열(10: 판매채널), K열(11: 판매가), L열(12: 택배비) 업데이트
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping}\n")
            time.sleep(2.0)

        print("🎉 모든 수집 및 구글 시트 업데이트 완료!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
