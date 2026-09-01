import os
import json
import time
import re
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup
from curl_cffi import requests

def extract_keywords(product_name, spec):
    """
    품목명과 규격에서 핵심 검색 단어(브랜드, 품목, 용량 등)를 세분화하여 추출합니다.
    """
    combined = f"{product_name} {spec}".lower()
    
    # 용량 단위 (920g, 1l, 5kg 등)
    capacities = re.findall(r'\d+\s*(?:g|kg|l|ml|ea)', combined)
    
    # 한글, 영문, 숫자 단어 추출
    words = re.findall(r'[가-힣a-zA-Z0-9]+', combined)
    
    required = set()
    for w in words:
        if len(w) >= 2 or w in ['g', 'l']:
            required.add(w)
            
    for cap in capacities:
        required.add(cap.replace(" ", ""))

    return required

def fetch_danawa_price_with_fallback(product_name, spec):
    """
    1차: 모든 키워드가 100% 완전 일치하는 본품 검색
    2차 (실패 시): 유사/대체 상품 검색
    """
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    
    if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE):
        search_query = clean_p
    else:
        search_query = f"{clean_p} {clean_s}".strip()

    print(f"  [다나와 수집 시작] 검색어: '{search_query}'")
    
    required_keywords = extract_keywords(product_name, spec)
    print(f"  └─ 🔍 1차 완전일치 검증 키워드: {list(required_keywords)}")

    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://search.danawa.com/dsearch.php?k1={encoded_query}&module=goods&act=dispMain"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "ko-KR,ko;q=0.9",
        "Referer": "https://www.danawa.com/"
    }

    try:
        response = requests.get(search_url, headers=headers, impersonate="chrome120", timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            products = soup.select("li.prod_item:not(.product-pot)")
            
            target_product = None
            is_exact_match = False

            # ----------------------------------------------------
            # 📌 1단계 시도: 모든 키워드가 100% 들어간 '완전 일치' 상품 탐색
            # ----------------------------------------------------
            for prod in products:
                title_el = prod.select_one("p.prod_name a")
                if not title_el:
                    continue
                
                title_text = title_el.text.strip()
                title_lower = title_text.lower().replace(" ", "")

                if all(kw in title_lower for kw in required_keywords):
                    target_product = prod
                    is_exact_match = True
                    print(f"  └─ 🎯 [1차 성공] 완전일치 본품 매칭: '{title_text}'")
                    break

            # ----------------------------------------------------
            # 📌 2단계 시도: 완전 일치가 없을 경우 '유사 상품' 탐색 (Fallback)
            # ----------------------------------------------------
            if not target_product and products:
                print("  └─ ⚠️ [1차 완전일치 실패] 2차 유사 상품 탐색으로 전환합니다.")
                for prod in products:
                    title_el = prod.select_one("p.prod_name a")
                    if title_el:
                        target_product = prod
                        print(f"  └─ 🔍 [2차 성공] 유사/대체 상품 매칭: '{title_el.text.strip()}'")
                        break

            # 상품 추출 처리
            if target_product:
                # 1. 판매가 (K열)
                price_el = target_product.select_one("p.price_sect a strong") or target_product.select_one("span.num")
                sale_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0

                # 2. 판매채널 (J열) - 유사 상품인 경우 안내 표시 선택 가능
                channel_el = target_product.select_one("div.memory_sect p.memory_mall") or target_product.select_one("p.mall_name")
                mall_text = channel_el.text.strip() if (channel_el and channel_el.text.strip()) else "다나와"
                channel_name = mall_text if is_exact_match else f"{mall_text}(유사)"

                # 3. 배송비 (L열)
                delivery_el = target_product.select_one("span.ship_fee") or target_product.select_one("div.delivery_sect")
                if delivery_el:
                    delivery_text = delivery_el.text.strip()
                    if "무료" in delivery_text:
                        shipping_fee = "무료배송"
                    else:
                        nums = re.findall(r'\d+', delivery_text.replace(",", ""))
                        shipping_fee = f"{int(nums[0]):,}원" if nums else "기본배송"
                else:
                    shipping_fee = "기본배송"

                # 4. 링크 (S열)
                link_el = target_product.select_one("p.prod_name a") or target_product.select_one("a.thumb_link")
                if link_el and link_el.get('href'):
                    product_link = link_el.get('href')
                    if product_link.startswith("//"):
                        product_link = "https:" + product_link
                else:
                    product_link = search_url

                if sale_price > 0:
                    return channel_name, sale_price, shipping_fee, product_link

    except Exception as e:
        print(f"  ❌ 수집 예외: {e}")

    return None, None, None, None

def run_price_update():
    try:
        # 1. 구글 인증 (GCP_TOKEN_JSON)
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
        print("📊 [우선순위 단계별 수집 가동] 3행부터 10행까지 조사를 진행합니다.")
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

            # 수집 가동 (1차: 완전일치 -> 2차: 유사상품)
            channel, price, shipping, link = fetch_danawa_price_with_fallback(product_name, spec)

            if not channel or price == 0:
                channel, price, shipping, link = "검색결과없음", 0, "-", "-"

            # 시트 업데이트
            worksheet.update_cell(row_num, 10, channel)   # J열: 판매채널
            worksheet.update_cell(row_num, 11, price)     # K열: 판매가
            worksheet.update_cell(row_num, 12, shipping)  # L열: 택배비

            # S열(19): 상품 링크 입력
            if link and link != "-":
                hyperlink_formula = f'=HYPERLINK("{link}", "링크보기")'
                worksheet.update_cell(row_num, 19, hyperlink_formula)
            else:
                worksheet.update_cell(row_num, 19, "-")

            print(f"  ✔ [완료] J={channel} | K={price:,}원 | L={shipping} | S=링크입력완료\n")
            time.sleep(2.0)

        print("🎉 단계별 정밀 수집 및 구글 시트 업데이트가 완벽히 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
