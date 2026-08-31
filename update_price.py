import os
import json
import time
import re
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup

def fetch_naver_price_html(search_query):
    """
    네이버 쇼핑 웹 페이지의 최신 HTML 구조에서 
    첫 번째 최저가 상품의 [판매채널, 판매가, 택배비]를 직접 추출합니다.
    """
    print(f"  [조사 요청] 검색어: '{search_query}'")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": "https://search.shopping.naver.com/"
    }
    
    encoded_query = requests.utils.quote(search_query)
    url = f"https://search.shopping.naver.com/search/all?where=all&frm=NVSCTAB&query={encoded_query}"
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 1. 네이버 쇼핑 결과에서 가격(price) 태그 찾기
            # 'price_num', 'price_price', 또는 숫자+원 형태 패턴 수집
            prices = soup.find_all(text=re.compile(r'[\d,]+원'))
            
            # 2. HTML 내부에 포함된 JSON 데이터(최저가 정밀 파싱)
            # 네이버 쇼핑은 __NEXT_DATA__ 태그 안에 전체 검색 결과 JSON이 들어있습니다.
            script_tag = soup.find('script', id='__NEXT_DATA__')
            if script_tag and script_tag.string:
                try:
                    raw_json = json.loads(script_tag.string)
                    products = raw_json['props']['pageProps']['initialResult']['shoppingResult']['products']
                    if products:
                        first = products[0]
                        channel = first.get('mallName') or "네이버쇼핑"
                        price = int(first.get('price') or first.get('lprice') or 0)
                        
                        delivery = first.get('deliveryFeeContent') or first.get('deliveryFee') or "기본배송"
                        if str(delivery) == '0' or '무료' in str(delivery):
                            shipping = "무료배송"
                        else:
                            shipping = f"{delivery}원" if isinstance(delivery, int) else str(delivery)
                            
                        return channel, price, shipping
                except Exception:
                    pass

            # HTML 일반 요소 접근 (구조 대응)
            product_card = soup.select_one("div[class*='product_item']") or soup.select_one("li[class*='basicList_item']")
            if product_card:
                channel_el = product_card.select_one("[class*='mall']") or product_card.select_one("[class*='seller']")
                channel = channel_el.text.strip() if channel_el else "네이버쇼핑"
                
                price_el = product_card.select_one("[class*='price']")
                if price_el:
                    nums = re.findall(r'\d+', price_el.text.replace(",", ""))
                    price = int("".join(nums)) if nums else 0
                else:
                    price = 0
                    
                delivery_el = product_card.select_one("[class*='delivery']")
                shipping = delivery_el.text.strip() if delivery_el else "기본배송"
                
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
        print("📊 [네이버 가격 추출 가동] 3행부터 10행까지 조사를 시작합니다.")
        print("==================================================\n")

        for row_num in range(3, 11):
            row_values = worksheet.row_values(row_num)
            
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열 (품목명)
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열 (규격)
            category = row_values[8] if len(row_values) >= 9 else ""      # I열 (구분)

            print(f"▶ [{row_num}행] 품목: '{product_name}' | 규격: '{spec}' | 구분(I열): '{category}'")

            # 예외 조건 체크 (전용, 예산, 종료 스킵)
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨 (구분 조건 제외: '{category}')\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨 (품목명 없음)\n")
                continue

            # 1차 검색 시도: 품목명 + 규격
            search_query = f"{product_name} {spec}".strip()
            channel, price, shipping = fetch_naver_price_html(search_query)

            # 2차 검색 시도 (실패 시): 품목명 단독
            if not channel or price == 0:
                print(f"  └─ ⚠️ 1차 실패 후 품목명 단독 재검색 시도: '{product_name}'")
                channel, price, shipping = fetch_naver_price_html(product_name.strip())

            if not channel or price == 0:
                channel, price, shipping = "검색결과없음", 0, "-"

            # 구글 시트 J열(10), K열(11), L열(12) 업데이트
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping}\n")
            time.sleep(2.0)

        print("🎉 수집 작업 완료!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
