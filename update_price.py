import os
import json
import time
import re
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup

def fetch_price_via_duckduckgo(product_name, spec):
    """
    DuckDuckGo 검색 엔진을 통해 네이버 쇼핑 상품을 우회 검색하고,
    검색 스니펫 및 링크 데이터에서 [판매채널, 최저가격, 배송비]를 추출합니다.
    """
    search_query = f"site:shopping.naver.com {product_name} {spec}".strip()
    clean_query = search_query.replace("*", " ")
    print(f"  [우회 조사 요청] 검색어: '{clean_query}'")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    
    # DuckDuckGo HTML 검색 URL
    ddg_url = f"https://html.duckduckgo.com/html/?q={requests.utils.quote(clean_query)}"
    
    try:
        response = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": clean_query},
            headers=headers,
            timeout=12
        )
        print(f"  [DDG 응답 상태] {response.status_code}")
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            results = soup.select(".result")
            
            for result in results:
                title_el = result.select_one(".result__title")
                snippet_el = result.select_one(".result__snippet")
                
                title = title_el.text.strip() if title_el else ""
                snippet = snippet_el.text.strip() if snippet_el else ""
                full_text = f"{title} {snippet}"
                
                # 1. 가격 추출 (예: "30,670원", "1,800원" 등)
                price_matches = re.findall(r'([\d,]+)\s*원', full_text)
                valid_prices = []
                for p in price_matches:
                    num = int(p.replace(",", ""))
                    if num >= 100:  # 유효한 상품 금액 판단
                        valid_prices.append(num)
                
                if valid_prices:
                    sale_price = min(valid_prices)
                    
                    # 2. 판매채널 추출
                    channel_name = "네이버쇼핑"
                    # 스니펫에서 쇼핑몰 이름 찾기
                    mall_match = re.search(r'([가-힣a-zA-Z0-9]+스토어|[가-힣a-zA-Z0-9]+몰|쿠팡|11번가|G마켓|옥션|SSG|스마트스토어)', full_text)
                    if mall_match:
                        channel_name = mall_match.group(1)

                    # 3. 배송비 추출
                    shipping_fee = "무료배송" if "무료배송" in full_text or "무료" in full_text else "기본배송"
                    
                    return channel_name, sale_price, shipping_fee

    except Exception as e:
        print(f"  ❌ DDG 수집 예외: {e}")

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
            credentials.refresh(Request())

        gc = gspread.authorize(credentials)

        # 2. 구글 시트 연결
        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        doc = gc.open_by_url(sheet_url)
        worksheet = doc.get_worksheet(0)

        print("==================================================")
        print("📊 [DuckDuckGo 우회 수집 가동] 3행부터 10행까지 조사를 시작합니다.")
        print("==================================================\n")

        for row_num in range(3, 11):
            row_values = worksheet.row_values(row_num)
            
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열 (품목명)
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열 (규격)
            category = row_values[8] if len(row_values) >= 9 else ""      # I열 (구분)

            print(f"▶ [{row_num}행] 품목: '{product_name}' | 규격: '{spec}' | 구분(I열): '{category}'")

            # 예외 조건 체크 ('전용', '예산', '종료'가 구분 항목에 들어있으면 스킵)
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨 (구분 조건 제외: '{category}')\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨 (품목명 없음)\n")
                continue

            # 1차 시도: 품목명 + 규격
            channel, price, shipping = fetch_price_via_duckduckgo(product_name, spec)

            # 2차 시도 (실패 시): 품목명 단독 (예: '서울우유 바리스타밀크 1L')
            if not channel or price == 0:
                print(f"  └─ ⚠️ 1차 실패 후 품목명 단독 재검색 시도: '{product_name}'")
                channel, price, shipping = fetch_price_via_duckduckgo(product_name, "")

            if not channel or price == 0:
                channel, price, shipping = "검색결과없음", 0, "-"

            # J열(10: 판매채널), K열(11: 판매가), L열(12: 택배비) 업데이트
            worksheet.update_cell(row_num, 10, channel)
            worksheet.update_cell(row_num, 11, price)
            worksheet.update_cell(row_num, 12, shipping)

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping}\n")
            time.sleep(2.5)  # 연속 요청 제한 방지용 대기시간

        print("🎉 해외 우회 조사를 통한 구글 시트 업데이트가 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
