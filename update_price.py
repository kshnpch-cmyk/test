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
    D열(규격) 텍스트에서 묶음 수량(EA, 개, 팩, Box 등)을 추출합니다.
    예: '300g*20ea' -> 20 | '1L*16EA' -> 16
    """
    if not spec_text:
        return 1
    
    match = re.search(r'\*\s*(\d+)\s*(?:ea|개|팩|box|박스|통|병|개입)?', spec_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
        
    match_unit = re.search(r'(\d+)\s*(?:ea|개|팩|box|박스|통|병)(?!\w)', spec_text, re.IGNORECASE)
    if match_unit:
        return int(match_unit.group(1))

    return 1

def extract_keywords(product_name, spec):
    """품목명과 규격에서 핵심 키워드를 추출합니다."""
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

def fetch_danawa_price(product_name, spec):
    """다나와 최저가 수집 (단품가 × 수량 계산)"""
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    pack_qty = extract_pack_quantity(spec)
    
    search_query = clean_p if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE) else f"{clean_p} {clean_s}".strip()
    required_keywords = extract_keywords(product_name, spec)

    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://search.danawa.com/dsearch.php?k1={encoded_query}&module=goods&act=dispMain"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.danawa.com/"
    }

    try:
        response = requests.get(search_url, headers=headers, impersonate="chrome120", timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            products = soup.select("li.prod_item:not(.product-pot)")
            
            target_product = None
            is_exact_match = False

            for prod in products:
                title_el = prod.select_one("p.prod_name a")
                if not title_el:
                    continue
                
                title_text = title_el.text.strip()
                title_lower = title_text.lower().replace(" ", "")

                if all(kw in title_lower for kw in required_keywords):
                    target_product = prod
                    is_exact_match = True
                    break

            if not target_product and products:
                target_product = products[0]

            if target_product:
                price_el = target_product.select_one("p.price_sect a strong") or target_product.select_one("span.num")
                single_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0
                total_sale_price = single_price * pack_qty

                channel_el = target_product.select_one("div.memory_sect p.memory_mall") or target_product.select_one("p.mall_name")
                mall_text = channel_el.text.strip() if (channel_el and channel_el.text.strip()) else "다나와"
                channel_name = mall_text if is_exact_match else f"{mall_text}(유사)"

                delivery_el = target_product.select_one("span.ship_fee") or target_product.select_one("div.delivery_sect")
                shipping_fee = ""
                if delivery_el:
                    delivery_text = delivery_el.text.strip()
                    nums = re.findall(r'\d+', delivery_text.replace(",", ""))
                    if nums and "무료" not in delivery_text:
                        shipping_fee = f"{int(nums[0]):,}원"

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

    return None, 0, "", "-"

def fetch_baemin_price(product_name, spec):
    """배민상회 최저가 수집 (단품가 × 수량 계산)"""
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    pack_qty = extract_pack_quantity(spec)
    
    search_query = clean_p if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE) else f"{clean_p} {clean_s}".strip()
    
    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://mart.baemin.com/search?keyword={encoded_query}"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
        "Referer": "https://mart.baemin.com/"
    }

    try:
        response = requests.get(search_url, headers=headers, impersonate="chrome120", timeout=10)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            # 배민상회 상품 카드 선택
            products = soup.select("a[class*='ProductItem']") or soup.select("li[class*='ProductList']") or soup.select("a[href*='/goods']")
            
            if products:
                first_prod = products[0]
                
                # 가격 추출
                price_el = first_prod.select_one("[class*='price']") or first_prod.select_one("span[class*='Price']")
                if price_el:
                    nums = re.findall(r'[\d,]+', price_el.text)
                    if nums:
                        single_price = int(nums[0].replace(",", ""))
                        total_price = single_price * pack_qty
                        
                        # 배민상회 상세 링크
                        href = first_prod.get('href', '')
                        product_link = f"https://mart.baemin.com{href}" if href.startswith('/') else search_url
                        
                        # 기본 배송비 (배민상회 배송 정책)
                        shipping_fee = ""
                        
                        return "배민상회", total_price, shipping_fee, product_link

    except Exception as e:
        print(f"  ❌ 배민상회 수집 예외: {e}")

    return "배민상회", 0, "", "-"

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

        all_rows = worksheet.get_all_values()
        total_rows = len(all_rows)

        print("==================================================")
        print(f"📊 [다나와 vs 배민상회 가격 비교 가동] 총 {total_rows}개 행을 처리합니다.")
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

            # 1. 다나와 수집
            d_channel, d_price, d_shipping, d_link = fetch_danawa_price(product_name, spec)
            
            # 2. 배민상회 수집
            b_channel, b_price, b_shipping, b_link = fetch_baemin_price(product_name, spec)

            # 3. 가격 비교 로직 (더 저렴한 채널 결정)
            final_channel, final_price, final_shipping, final_link = d_channel, d_price, d_shipping, d_link

            if b_price > 0:
                if d_price == 0 or b_price < d_price:
                    print(f"  💡 [배민상회 우세] 배민({b_price:,}원) < 다나와({d_price:,}원) ➔ 배민상회로 덮어씁니다.")
                    final_channel, final_price, final_shipping, final_link = b_channel, b_price, b_shipping, b_link
                else:
                    print(f"  ⚖️ [다나와 우세/동일] 다나와({d_price:,}원) <= 배민({b_price:,}원) ➔ 다나와 유지")

            if final_price == 0:
                final_channel, final_price, final_shipping, final_link = "검색결과없음", 0, "", "-"

            # 구글 시트 업데이트
            worksheet.update_cell(row_idx, 10, final_channel)   # J열: 판매채널
            worksheet.update_cell(row_idx, 11, final_price)     # K열: 판매가
            worksheet.update_cell(row_idx, 12, final_shipping)  # L열: 택배비

            # S열(19): 링크 수식 업데이트
            if final_link and final_link != "-":
                hyperlink_formula = f'=HYPERLINK("{final_link}", "링크보기")'
                worksheet.update_cell(row_idx, 19, hyperlink_formula)
            else:
                worksheet.update_cell(row_idx, 19, "-")

            disp_shipping = final_shipping if final_shipping else "공란(무료/없음)"
            print(f"  ✔ [완료] 선택채널={final_channel} | 최저가={final_price:,}원 | 배송비={disp_shipping}\n")

            time.sleep(2.0)

            if row_idx % 50 == 0:
                print("  💤 API 요청 안정화를 위해 5초간 대기합니다...\n")
                time.sleep(5)

        print("🎉 다나와 vs 배민상회 최저가 비교 및 구글 시트 업데이트가 완벽히 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
