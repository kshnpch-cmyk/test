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
    네이버 쇼핑 검색을 통해 최저가 판매채널(J열), 판매가(K열), 택배비(L열)를 수집합니다.
    """
    print(f"🔍 검색 시도 키워드: [{search_query}]")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://search.shopping.naver.com/"
    }
    
    search_url = f"https://search.shopping.naver.com/search/all?query={requests.utils.quote(search_query)}"
    
    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 네이버 쇼핑의 다양한 상품 목록 클래스 태그 감지
            first_product = (
                soup.select_one("div[class*='product_item']") or 
                soup.select_one("li[class*='basicList_item']") or
                soup.select_one("div[class*='adProduct_item']")
            )
            
            if first_product:
                # 1. 판매채널 (J열)
                channel_el = (
                    first_product.select_one("[class*='product_mall']") or 
                    first_product.select_one("[class*='basicList_mall']") or
                    first_product.select_one("[class*='product_link']")
                )
                channel_name = channel_el.text.strip() if channel_el else "네이버쇼핑"
                
                # 2. 판매가 (K열)
                price_el = (
                    first_product.select_one("[class*='price_num']") or 
                    first_product.select_one("[class*='price_price']") or
                    first_product.select_one("span[class*='price']")
                )
                if price_el:
                    # 숫자만 추출
                    import re
                    price_nums = re.findall(r'\d+', price_el.text.replace(",", ""))
                    sale_price = int("".join(price_nums)) if price_nums else 0
                else:
                    sale_price = 0
                
                # 3. 배송비 (L열)
                delivery_el = (
                    first_product.select_one("[class*='price_delivery']") or 
                    first_product.select_one("[class*='basicList_delivery']")
                )
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

    return None, None, None

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

        print("📊 3행부터 10행까지 가격 조사를 시작합니다...\n")

        for row_num in range(3, 11):
            row_values = worksheet.row_values(row_num)
            
            category = row_values[1] if len(row_values) >= 2 else ""      # B열: 구분
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열: 품목명
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열: 규격
            note = row_values[13] if len(row_values) >= 14 else ""        # N열: 특이사항

            # 예외 조건 체크
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"⏭️ [{row_num}행] 스킵됨 (구분 조건 제외: '{category}')")
                continue

            if not product_name.strip():
                print(f"⏭️ [{row_num}행] 스킵됨 (품목명 없음)")
                continue

            # 1차 시도: C열 + D열 + N열 전체 조합
            full_query = " ".join([k.strip() for k in [product_name, spec, note] if k.strip()])
            channel, price, shipping = fetch_naver_price(full_query)

            # 2차 시도 (전체 검색 결과가 없을 경우): N열(특이사항) 제외하고 C열 + D열로만 검색
            if not channel and note.strip():
                short_query = " ".join([k.strip() for k in [product_name, spec] if k.strip()])
                print(f"  └─ ⚠️ 1차 검색 실패. 2차 간소화 키워드로 재검색: [{short_query}]")
                channel, price, shipping = fetch_naver_price(short_query)

            # 3차 시도 (여전히 없을 경우): C열(품목명) 단독 검색
            if not channel and spec.strip():
                basic_query = product_name.strip()
                print(f"  └─ ⚠️ 2차 검색 실패. 3차 품목명 단독 재검색: [{basic_query}]")
                channel, price, shipping = fetch_naver_price(basic_query)

            # 최종 실패 시
            if not channel:
                channel, price, shipping = "검색결과없음", 0, "-"

            # 구글 시트 J열(10), K열(11), L열(12) 업데이트
            worksheet.update_cell(row_num, 10, channel)   # J열: 판매채널
            worksheet.update_cell(row_num, 11, price)     # K열: 판매가
            worksheet.update_cell(row_num, 12, shipping)  # L열: 택배비

            print(f"  └─ ✅ [{row_num}행 결과] J(채널): {channel} | K(가격): {price}원 | L(택배비): {shipping}\n")

            time.sleep(2.0)

        print("🎉 3행~10행 가격 수집 및 시트 업데이트 완료!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
