import os
import json
import time
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def fetch_naver_price_bypass(search_query):
    """
    네이버 모바일 내부 API를 활용하여 418 차단을 우회하고
    판매채널(J열), 최저가(K열), 택배비(L열) 데이터를 가져옵니다.
    """
    clean_query = search_query.replace("*", " ").strip()
    print(f"  [조사 요청] 검색어: '{clean_query}'")
    
    # 418 차단 우회용 네이버 모바일 전용 헤더
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://msearch.shopping.naver.com/",
        "Accept-Language": "ko-KR,ko;q=0.9"
    }
    
    encoded_query = requests.utils.quote(clean_query)
    # 418 차단을 받지 않는 네이버 모바일 내부 API 주소
    api_url = f"https://msearch.shopping.naver.com/api/search/all?query={encoded_query}&sort=rel"
    
    try:
        response = requests.get(api_url, headers=headers, timeout=10)
        print(f"  [응답 코드] {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            # JSON 내 상품 리스트 경로 찾기
            shopping_res = data.get('props', {}).get('pageProps', {}).get('initialResult', {}).get('shoppingResult', {})
            products = shopping_res.get('products', [])
            
            if not products:
                products = data.get('shoppingResult', {}).get('products', [])

            if products:
                first = products[0]
                
                # 1. J열: 판매채널
                channel = first.get('mallName') or first.get('mallInfoCache', {}).get('name') or "네이버쇼핑"
                
                # 2. K열: 최저가
                price_val = first.get('price') or first.get('lprice') or 0
                price = int(price_val)
                
                # 3. L열: 택배비
                delivery_fee = first.get('deliveryFee', 0)
                delivery_txt = str(first.get('deliveryFeeContent', ''))
                
                if delivery_fee == 0 or "무료" in delivery_txt:
                    shipping = "무료배송"
                else:
                    shipping = f"{delivery_fee}원" if isinstance(delivery_fee, int) else delivery_txt

                return channel, price, shipping
            else:
                print("  ⚠️ [200 성공했으나 데이터 없음] 검색 결과 상품 배열이 비어있습니다.")

    except Exception as e:
        print(f"  ❌ 예외 발생: {e}")

    return None, None, None

def run_price_update():
    try:
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

        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        doc = gc.open_by_url(sheet_url)
        worksheet = doc.get_worksheet(0)

        print("==================================================")
        print("📊 [우회 API 조사 시작] 3행부터 10행까지 조사를 진행합니다.")
        print("==================================================\n")

        for row_num in range(3, 11):
            row_values = worksheet.row_values(row_num)
            
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열 (품목명)
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열 (규격)
            category = row_values[8] if len(row_values) >= 9 else ""      # I열 (구분)

            print(f"▶ [{row_num}행] 품목: '{product_name}' | 규격: '{spec}' | 구분(I열): '{category}'")

            # 예외 조건 체크 ('전용', '예산', '종료'가 구분 항목에 들어있으면 건너뜀)
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨 (구분 조건 제외: '{category}')\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨 (품목명 없음)\n")
                continue

            # 1차 시도: C열(품목명) + D열(규격)
            search_query = f"{product_name} {spec}".strip()
            channel, price, shipping = fetch_naver_price_bypass(search_query)

            # 2차 시도 (실패 시): C열(품목명) 단독 검색 (예: '서울우유 바리스타밀크 1L')
            if not channel or price == 0:
                print(f"  └─ ⚠️ 1차 실패 후 품목명 단독 재검색 시도: '{product_name}'")
                channel, price, shipping = fetch_naver_price_bypass(product_name.strip())

            if not channel or price == 0:
                channel, price, shipping = "검색결과없음", 0, "-"

            # J열(10: 판매채널), K열(11: 판매가), L열(12: 택배비) 업데이트
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping}\n")
            time.sleep(2.0)

        print("🎉 수집 작업 및 구글 시트 업데이트가 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
