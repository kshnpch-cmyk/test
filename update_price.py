import os
import json
import time
import requests
from bs4 import BeautifulSoup
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def fetch_naver_price(search_query):
    """
    네이버 쇼핑 검색을 통해 최저가 판매채널(J열), 판매가(K열), 택배비(L열)를 크롤링합니다.
    """
    print(f"🔍 검색 키워드: [{search_query}]")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    search_url = f"https://search.shopping.naver.com/search/all?query={search_query}"
    
    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 첫 번째 상품 추출
            first_product = soup.select_one("div[class*='product_item']") or soup.select_one("li[class*='basicList_item']")
            
            if first_product:
                # 1. 판매채널 (J열)
                channel_el = first_product.select_one("[class*='product_mall']") or first_product.select_one("[class*='basicList_mall']")
                channel_name = channel_el.text.strip() if channel_el else "네이버쇼핑"
                
                # 2. 판매가 (K열)
                price_el = first_product.select_one("[class*='price_num']") or first_product.select_one("[class*='price_price']")
                if price_el:
                    sale_price = int(price_el.text.replace(",", "").replace("원", "").strip())
                else:
                    sale_price = 0
                
                # 3. 배송비 (L열)
                delivery_el = first_product.select_one("[class*='price_delivery']") or first_product.select_one("[class*='basicList_delivery']")
                if delivery_el:
                    delivery_text = delivery_el.text.strip()
                    if "무료" in delivery_text:
                        shipping_fee = "무료배송"
                    else:
                        import re
                        nums = re.findall(r'\d+', delivery_text.replace(",", ""))
                        shipping_fee = int(nums[0]) if nums else delivery_text
                else:
                    shipping_fee = "기본배송"

                return channel_name, sale_price, shipping_fee

    except Exception as e:
        print(f"  └─ 크롤링 에러: {e}")

    return "검색결과없음", 0, "-"

def run_price_update():
    try:
        # 1. 구글 인증 처리
        token_secret = os.environ.get('GCP_TOKEN_JSON')
        if not token_secret:
            raise ValueError("❌ GitHub Secret에 'GCP_TOKEN_JSON'이 설정되지 않았습니다.")
        
        token_info = json.loads(token_secret)
        token_info.pop("scopes", None)
        token_info.pop("scope", None)

        credentials = Credentials.from_authorized_user_info(token_info)
        credentials._scopes = None

        if credentials.expired and credentials.refresh_token:
            print("🔄 토큰 갱신 중...")
            credentials.refresh(Request())

        gc = gspread.authorize(credentials)

        # 2. 구글 시트 접속
        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        doc = gc.open_by_url(sheet_url)
        worksheet = doc.get_worksheet(0)

        print("📊 3행부터 10행까지 조사를 시작합니다...\n")

        # 3. 3행부터 10행까지만 제한 실행
        for row_num in range(3, 11):
            # 행 전체 데이터 읽기 (1행부터 시작하는 열 번호 기준)
            row_values = worksheet.row_values(row_num)
            
            # 열 위치 정의 (C=3, D=4, N=14 기준 / 구분이 2열(B열)에 위치해 있다고 가정)
            # 안전한 추출을 위해 인덱스 범위 체크
            category = row_values[1] if len(row_values) >= 2 else ""      # 구분 (예: B열)
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열: 품목명
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열: 규격
            note = row_values[13] if len(row_values) >= 14 else ""        # N열: 특이사항

            # 예외 조건 체크 ('전용', '예산', '종료'가 구분 항목에 들어있으면 건너뜀)
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"⏭️ [{row_num}행] 스킵됨 (구분 조건 제외: '{category}')")
                continue

            # 품목명이 없으면 건너뜀
            if not product_name.strip():
                print(f"⏭️ [{row_num}행] 스킵됨 (품목명 없음)")
                continue

            # C열 + D열 + N열 키워드 조합
            keywords = [k.strip() for k in [product_name, spec, note] if k.strip()]
            search_query = " ".join(keywords)

            # 네이버 쇼핑 수집
            channel, price, shipping = fetch_naver_price(search_query)

            # 구글 시트 J열(10), K열(11), L열(12) 업데이트
            worksheet.update_cell(row_num, 10, channel)   # J열: 판매채널
            worksheet.update_cell(row_num, 11, price)     # K열: 판매가
            worksheet.update_cell(row_num, 12, shipping)  # L열: 택배비

            print(f"  └─ ✅ [{row_num}행] J(채널): {channel} | K(가격): {price}원 | L(택배비): {shipping}\n")

            # 네이버 요청 제한 방지용 대기시간
            time.sleep(2.0)

        print("🎉 3행~10행 가격 수집 및 시트 업데이트 완료!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
