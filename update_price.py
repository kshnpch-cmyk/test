import os
import json
import time
import re
import gspread
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup
from curl_cffi import requests

def extract_pack_quantity(spec_text):
    """
    D열(규격) 텍스트에서 묶음 수량(EA, 개, 팩, Box 등)을 자동 추출합니다.
    예: '300g*20ea' -> 20 | '1L*16EA' -> 16 | '1KG*6팩' -> 6
    수량을 찾지 못하면 기본값 1을 반환합니다.
    """
    if not spec_text:
        return 1
    
    # 1. *20ea, *20개, *20팩 형태 추출
    match = re.search(r'\*\s*(\d+)\s*(?:ea|개|팩|box|박스|통|병|개입)?', spec_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
        
    # 2. 20ea, 20개 등 숫자+단위 추출
    match_unit = re.search(r'(\d+)\s*(?:ea|개|팩|box|박스|통|병)(?!\w)', spec_text, re.IGNORECASE)
    if match_unit:
        return int(match_unit.group(1))

    return 1

def extract_keywords(product_name, spec):
    """
    품목명과 규격에서 핵심 검색 단어(브랜드, 품목, 용량 등)를 추출합니다.
    """
    combined = f"{product_name} {spec}".lower()
    capacities = re.findall(r'\d+\s*(?:g|kg|l|ml|ea)', combined)
    words = re.findall(r'[가-힣a-zA-Z0-9]+', combined)
    
    required = set()
    for w in words:
        if len(w) >= 2 or w in ['g', 'l']:
            required.add(w)
            
    for cap in capacities:
        required.add(cap.replace(" ", ""))

    return required

def fetch_danawa_price_adjusted(product_name, spec):
    """
    다나와에서 최저가를 수집한 뒤, 우리 시트 규격 수량(예: *20ea)을 계산하여
    총 최저가, 판매채널, 배송비(있으면 금액, 없으면 공란), 링크를 반환합니다.
    """
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    
    # 규격 수량 파싱 (예: 20EA -> 20)
    pack_qty = extract_pack_quantity(spec)
    
    # 검색어 정제 (수량 단위 단독 검색어 배제)
    if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE):
        search_query = clean_p
    else:
        # 단품 단위 용량 파악을 위해 규격 조합
        search_query = f"{clean_p} {clean_s}".strip()

    print(f"  [조사 요청] 검색어: '{search_query}' (규격 계산 수량: {pack_qty}개)")
    required_keywords = extract_keywords(product_name, spec)

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
            # 1단계 시도: '완전일치' 상품 탐색
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
                    print(f"  └─ 🎯 [1차 완전일치]: '{title_text}'")
                    break

            # ----------------------------------------------------
            # 2단계 시도: 완전일치 실패 시 '유사 상품' 탐색
            # ----------------------------------------------------
            if not target_product and products:
                for prod in products:
                    title_el = prod.select_one("p.prod_name a")
                    if title_el:
                        target_product = prod
                        print(f"  └─ 🔍 [2차 유사매칭]: '{title_el.text.strip()}'")
                        break

            # 정보 추출 및 우리 시트 규격 수량 맞춤 계산
            if target_product:
                # 1. 단품 가격 추출
                price_el = target_product.select_one("p.price_sect a strong") or target_product.select_one("span.num")
                single_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0

                # 2. 우리 시트 규격 수량(pack_qty)에 맞춰 총 금액 계산
                total_sale_price = single_price * pack_qty

                # 3. 판매채널 (J열)
                channel_el = target_product.select_one("div.memory_sect p.memory_mall") or target_product.select_one("p.mall_name")
                mall_text = channel_el.text.strip() if (channel_el and channel_el.text.strip()) else "다나와"
                channel_name = mall_text if is_exact_match else f"{mall_text}(유사)"

                # 4. 배송비 (L열) - 있으면 금액, 무료/미확인은 공란("") 처리
                delivery_el = target_product.select_one("span.ship_fee") or target_product.select_one("div.delivery_sect")
                shipping_fee = ""
                if delivery_el:
                    delivery_text = delivery_el.text.strip()
                    nums = re.findall(r'\d+', delivery_text.replace(",", ""))
                    if nums and "무료" not in delivery_text:
                        shipping_fee = f"{int(nums[0]):,}원"

                # 5. 링크 (S열)
                link_el = target_product.select_one("p.prod_name a") or target_product.select_one("a.thumb_link")
                if link_el and link_el.get('href'):
                    product_link = link_el.get('href')
                    if product_link.startswith("//"):
                        product_link = "https:" + product_link
                else:
                    product_link = search_url

                if total_sale_price > 0:
                    return channel_name, total_sale_price, shipping_fee, product_link

    except Exception as e:
        print(f"  ❌ 다나와 수집 예외: {e}")

    return None, None, None, None

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

        # 전체 데이터 로드
        all_rows = worksheet.get_all_values()
        total_rows = len(all_rows)

        print("==================================================")
        print(f"📊 [규격 수량 맞춤 계산 가동] 총 {total_rows}개 행 수집을 시작합니다.")
        print("==================================================\n")

        for row_idx in range(3, total_rows + 1):
            row_values = all_rows[row_idx - 1] if (row_idx - 1) < len(all_rows) else []

            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열 (품목명)
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열 (규격)
            category = row_values[8] if len(row_values) >= 9 else ""      # I열 (구분)

            print(f"▶ [{row_idx}/{total_rows}행] 품목: '{product_name}' | 규격: '{spec}' | 구분(I열): '{category}'")

            # 스킵 조건: I열(구분)에 '전용', '예산', '종료'가 포함된 경우
            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨 (구분 조건 제외: '{category}')\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨 (품목명 없음)\n")
                continue

            # 다나와 가격 수집 및 규격 수량 맞춤 자동 계산
            channel, price, shipping, link = fetch_danawa_price_adjusted(product_name, spec)

            if not channel or price == 0:
                channel, price, shipping, link = "검색결과없음", 0, "", "-"

            # 시트 업데이트 (J=10: 채널, K=11: 총금액, L=12: 배송비, S=19: 링크)
            worksheet.update_cell(row_idx, 10, channel)
            worksheet.update_cell(row_idx, 11, price)
            worksheet.update_cell(row_idx, 12, shipping)

            if link and link != "-":
                hyperlink_formula = f'=HYPERLINK("{link}", "링크보기")'
                worksheet.update_cell(row_idx, 19, hyperlink_formula)
            else:
                worksheet.update_cell(row_idx, 19, "-")

            disp_shipping = shipping if shipping else "공란(무료/없음)"
            print(f"  ✔ [완료] J={channel} | K={price:,}원(총액) | L={disp_shipping} | S=링크완료\n")

            time.sleep(1.8)

            if row_idx % 50 == 0:
                print("  💤 API 요청 안정화를 위해 5초간 대기합니다...\n")
                time.sleep(5)

        print("🎉 전체 품목 규격 수량 맞춤 정산 및 시트 업데이트가 완벽히 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
