import os
import json
import time
import requests
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

def fetch_naver_price_debug(search_query):
    """
    네이버 쇼핑에 검색을 요청하고,
    응답 상태 및 수신받은 실제 내용을 진단 로그로 출력합니다.
    """
    print(f"  [디버그] 🔎 네이버 검색 요청 키워드: '{search_query}'")
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9"
    }
    
    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://search.shopping.naver.com/search/all?query={encoded_query}"
    
    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        print(f"  [디버그] 📡 네이버 응답 상태 코드: {response.status_code}")
        
        if response.status_code == 200:
            html_text = response.text
            print(f"  [디버그] 📄 응답받은 HTML 전체 길이: {len(html_text)} 글자")
            
            # 차단 여부 확인 (CAPTCHA나 차단 안내가 포함되었는지)
            if "captcha" in html_text.lower() or "blocked" in html_text.lower():
                print("  [디버그] ⚠️ 네이버에서 봇(Bot) 차단 페이지를 반환했습니다.")
                return "네이버차단", 0, "-"
            
            # HTML 내부에서 상품 정보 키워드가 존재하는지 체크
            if "product_item" in html_text or "basicList_item" in html_text or "price_num" in html_text:
                print("  [디버그] ✅ 네이버 응답 본문에서 상품 목록 HTML 태그를 발견했습니다!")
            else:
                print("  [디버그] ❌ 네이버 응답 본문에 상품 목록 태그가 존재하지 않습니다. (검색어 미매칭 또는 구조 변경)")
                # 응답 본문 앞부분 300자 출력해보기
                print(f"  [디버그] 📝 HTML 본문 미리보기:\n{html_text[:300]}...")
                
    except Exception as e:
        print(f"  [디버그] ❌ 네트워크/크롤링 에러 발생: {e}")

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
            print("🔄 구글 토큰 갱신 중...")
            credentials.refresh(Request())

        gc = gspread.authorize(credentials)

        # 2. 구글 시트 접속
        sheet_url = "https://docs.google.com/spreadsheets/d/1nA0ZtCztY6Qe8UR8-erB98_BIZDYUUjpIQYWNyuePWA/edit"
        doc = gc.open_by_url(sheet_url)
        worksheet = doc.get_worksheet(0)

        print("==================================================")
        print("📊 [시트 진단 시작] 3행부터 10행까지 데이터를 확인합니다.")
        print("==================================================\n")

        for row_num in range(3, 11):
            row_values = worksheet.row_values(row_num)
            
            # 각 열의 읽어온 진짜 데이터 출력
            print(f"▶ [{row_num}행] 전체 읽어온 값 (총 {len(row_values)}개 열): {row_values}")
            
            category = row_values[1] if len(row_values) >= 2 else ""      # B열: 구분
            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열: 품목명
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열: 규격
            note = row_values[13] if len(row_values) >= 14 else ""        # N열: 특이사항

            print(f"  ├─ 구분(B열): '{category}'")
            print(f"  ├─ 품목명(C열): '{product_name}'")
            print(f"  ├─ 규격(D열): '{spec}'")
            print(f"  └─ 특이사항(N열): '{note}'")

            # 예외 조건 체크
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨: '구분' 항목에 제외 단어('{category}')가 포함되어 있습니다.\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨: 품목명(C열)이 비어있습니다.\n")
                continue

            # 검색 키워드 조합
            full_query = " ".join([k.strip() for k in [product_name, spec, note] if k.strip()])
            
            # 네이버 진단 검색
            channel, price, shipping = fetch_naver_price_debug(full_query)

            # 시트 업데이트 테스트
            worksheet.update_cell(row_num, 10, channel)   # J열
            worksheet.update_cell(row_num, 11, price)     # K열
            worksheet.update_cell(row_num, 12, shipping)  # L열

            print(f"  ✔ [{row_num}행 완료] J={channel} | K={price} | L={shipping}\n")
            time.sleep(1.5)

        print("==================================================")
        print("🎉 진단 작업 완료!")

    except Exception as e:
        print(f"❌ 전체 로직 중 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
