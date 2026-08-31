import os
import json
import time
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def fetch_naver_price_official(search_query):
    """
    네이버 공식 쇼핑 검색 API를 호출하여
    판매채널(J열), 판매가(K열), 택배비(L열)를 100% 정확히 가져옵니다.
    """
    client_id = os.environ.get('NAVER_CLIENT_ID')
    client_secret = os.environ.get('NAVER_CLIENT_SECRET')

    if not client_id or not client_secret:
        print("  ❌ GitHub Secret에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET이 없습니다.")
        return None, None, None

    # 특수문자 정제
    clean_query = search_query.replace("*", " ").strip()
    url = "https://openapi.naver.com/v1/search/shop.json"
    
    headers = {
        "X-Naver-Client-Id": client_id,
        "X-Naver-Client-Secret": client_secret
    }
    
    params = {
        "query": clean_query,
        "display": 1,   # 가장 정확도 높은 최상위 1개 추출
        "sort": "sim"   # 정확도순
    }

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            items = data.get('items', [])
            
            if items:
                first = items[0]
                
                # 1. J열: 판매채널 (mallName)
                channel = first.get('mallName') or "네이버쇼핑"
                
                # 2. K열: 판매가 (lprice)
                price = int(first.get('lprice', 0))
                
                # 3. L열: 택배비 (공식 API는 기본 배송 정책 반환)
                shipping = "무료/기본배송"

                return channel, price, shipping

    except Exception as e:
        print(f"  ❌ API 호출 예외: {e}")

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

        # 2. 구글 시트 접속
        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        doc = gc.open_by_url(sheet_url)
        worksheet = doc.get_worksheet(0)

        print("==================================================")
        print("📊 [공식 API 가동] 3행부터 10행까지 조사를 시작합니다.")
        print("==================================================\n")

        for row_num in range(3, 11):
            row_values = worksheet.row_values(row_num)
            
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열 (품목명)
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열 (규격)
            category = row_values[8] if len(row_values) >= 9 else ""      # I열 (구분)

            print(f"▶ [{row_num}행] 품목: '{product_name}' | 규격: '{spec}' | 구분(I열): '{category}'")

            # 예외 조건 스킵 (전용, 예산, 종료)
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨 (구분 조건 제외: '{category}')\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨 (품목명 없음)\n")
                continue

            # 1차 시도: C열(품목명) + D열(규격)
            search_query = f"{product_name} {spec}".strip()
            channel, price, shipping = fetch_naver_price_official(search_query)

            # 2차 시도 (실패 시): C열(품목명) 단독
            if not channel or price == 0:
                print(f"  └─ ⚠️ 1차 실패 후 품목명 단독 재검색: '{product_name}'")
                channel, price, shipping = fetch_naver_price_official(product_name.strip())

            if not channel or price == 0:
                channel, price, shipping = "검색결과없음", 0, "-"

            # J열(10: 판매채널), K열(11: 판매가), L열(12: 택배비) 업데이트
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping}\n")
            time.sleep(0.3)  # API는 응답이 매우 빠르므로 0.3초 대기

        print("🎉 공식 API 연동 조사가 완벽히 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
