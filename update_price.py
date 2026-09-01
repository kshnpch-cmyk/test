import os
import json
import time
import re
import gspread
from google import genai
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup
from curl_cffi import requests

# Gemini API 클라이언트 초기화
gemini_api_key = os.environ.get('GEMINI_API_KEY')
ai_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None

def analyze_product_with_gemini(sheet_product, sheet_spec, crawled_title, crawled_price):
    """
    Gemini AI를 호출하여 검색된 상품명의 실제 묶음 수량을 파싱하고,
    시트 규격에 맞는 단가를 정밀 계산합니다.
    """
    if not ai_client:
        print("  ⚠️ GEMINI_API_KEY가 설정되지 않아 기본 정규식 파싱으로 대체합니다.")
        return crawled_price, True

    prompt = f"""
너는 쇼핑몰 데이터 분석 전문가야.
아래 [구글 시트 요청 정보]와 크롤링한 [쇼핑몰 검색 결과]를 비교 분석해줘.

[구글 시트 요청 정보]
- 품목명: {sheet_product}
- 규격: {sheet_spec}

[쇼핑몰 검색 결과]
- 검색된 상품명: {crawled_title}
- 검색된 표시 가격: {crawled_price}원

다음 질문에 맞춰 오직 JSON 형식으로만 응답해:
1. "is_matched": 검색된 상품이 구글 시트 요청 품목과 동일한 종류의 상품인지 여부 (true/false)
2. "crawled_unit_qty": 검색된 상품명(예: '1kg (3개)') 속에 포함된 제품 개수 (숫자만, 예: 3). 단품이면 1
3. "single_unit_price": 검색된 표시 가격을 개수로 나눈 '1개당 단가' (숫자만)
4. "reason": 판단 이유 요약

응답 형식(JSON):
{{
  "is_matched": true,
  "crawled_unit_qty": 3,
  "single_unit_price": 5770,
  "reason": "1kg 3개 묶음 17310원이므로 1개당 단가는 5770원입니다."
}}
"""

    try:
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config={'response_mime_type': 'application/json'}
        )
        
        result = json.loads(response.text)
        is_matched = result.get("is_matched", True)
        single_price = result.get("single_unit_price", crawled_price)
        reason = result.get("reason", "")
        
        print(f"  🤖 [Gemini 분석] {reason}")
        return single_price, is_matched

    except Exception as e:
        print(f"  ❌ Gemini 분석 오류: {e}")
        return crawled_price, True

def extract_pack_quantity(spec_text):
    """D열(규격) 텍스트에서 시트 수량(EA, 개 등)을 추출합니다."""
    if not spec_text:
        return 1
    match = re.search(r'\*\s*(\d+)\s*(?:ea|개|팩|box|박스|통|병|개입)?', spec_text, re.IGNORECASE)
    if match:
        return int(match.group(1))
    match_unit = re.search(r'(\d+)\s*(?:ea|개|팩|box|박스|통|병)(?!\w)', spec_text, re.IGNORECASE)
    if match_unit:
        return int(match_unit.group(1))
    return 1

def fetch_danawa_price(product_name, spec):
    """다나와 최저가 수집 후 Gemini AI 검증"""
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    target_qty = extract_pack_quantity(spec)
    
    search_query = clean_p if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE) else f"{clean_p} {clean_s}".strip()
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
            
            if products:
                first_prod = products[0]
                title_el = first_prod.select_one("p.prod_name a")
                title_text = title_el.text.strip() if title_el else ""

                price_el = first_prod.select_one("p.price_sect a strong") or first_prod.select_one("span.num")
                raw_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0

                if raw_price > 0:
                    # 🤖 Gemini AI를 이용한 단위 가격 분석
                    single_price, is_matched = analyze_product_with_gemini(product_name, spec, title_text, raw_price)
                    
                    if is_matched:
                        total_price = int(single_price * target_qty)

                        channel_el = first_prod.select_one("div.memory_sect p.memory_mall") or first_prod.select_one("p.mall_name")
                        mall_text = channel_el.text.strip() if (channel_el and channel_el.text.strip()) else "다나와"

                        delivery_el = first_prod.select_one("span.ship_fee") or first_prod.select_one("div.delivery_sect")
                        shipping_fee = ""
                        if delivery_el:
                            delivery_text = delivery_el.text.strip()
                            nums = re.findall(r'\d+', delivery_text.replace(",", ""))
                            if nums and "무료" not in delivery_text:
                                shipping_fee = f"{int(nums[0]):,}원"

                        link_el = first_prod.select_one("p.prod_name a") or first_prod.select_one("a.thumb_link")
                        href = link_el.get('href') if link_el else ""
                        product_link = f"https:{href}" if href.startswith("//") else (href or search_url)

                        return mall_text, total_price, shipping_fee, product_link, title_text

    except Exception as e:
        print(f"  ❌ 다나와 수집 예외: {e}")

    return "다나와", 0, "", "-", ""

def fetch_baemin_price(product_name, spec):
    """배민상회 최저가 수집 후 Gemini AI 검증"""
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    target_qty = extract_pack_quantity(spec)
    
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
            products = soup.select("a[class*='ProductItem']") or soup.select("li[class*='ProductList']") or soup.select("a[href*='/goods']")
            
            if products:
                first_prod = products[0]
                title_el = first_prod.select_one("[class*='title']") or first_prod.select_one("[class*='Name']") or first_prod
                title_text = title_el.text.strip() if title_el else ""

                price_el = first_prod.select_one("[class*='price']") or first_prod.select_one("span[class*='Price']")
                if price_el:
                    nums = re.findall(r'[\d,]+', price_el.text)
                    if nums:
                        raw_price = int(nums[0].replace(",", ""))
                        
                        # 🤖 Gemini AI를 이용한 단위 가격 분석
                        single_price, is_matched = analyze_product_with_gemini(product_name, spec, title_text, raw_price)
                        
                        if is_matched:
                            total_price = int(single_price * target_qty)
                            href = first_prod.get('href', '')
                            product_link = f"https://mart.baemin.com{href}" if href.startswith('/') else search_url
                            
                            return "배민상회", total_price, "", product_link, title_text

    except Exception as e:
        print(f"  ❌ 배민상회 수집 예외: {e}")

    return "배민상회", 0, "", "-", ""

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

        all_rows = worksheet.get_all_values()
        total_rows = len(all_rows)

        print("==================================================")
        print(f"📊 [Gemini AI 정밀 검증 가동] 총 {total_rows}개 행 수집을 시작합니다.")
        print("==================================================\n")

        for row_idx in range(3, total_rows + 1):
            row_values = all_rows[row_idx - 1] if (row_idx - 1) < len(all_rows) else []

            product_name = row_values[2] if len(row_values) >= 3 else ""  # C열
            spec = row_values[3] if len(row_values) >= 4 else ""          # D열
            category = row_values[8] if len(row_values) >= 9 else ""      # I열

            print(f"▶ [{row_idx}/{total_rows}행] 품목: '{product_name}' | 규격: '{spec}' | 구분: '{category}'")

            if any(skip_word in category for skip_word in ['전용', '예산', '종료']):
                print(f"  ⏭️ 스킵됨 (구분 조건 제외: '{category}')\n")
                continue

            if not product_name.strip():
                print(f"  ⏭️ 스킵됨 (품목명 없음)\n")
                continue

            # 1. 다나와 수집 및 Gemini 분석
            d_channel, d_price, d_shipping, d_link, d_title = fetch_danawa_price(product_name, spec)
            
            # 2. 배민상회 수집 및 Gemini 분석
            b_channel, b_price, b_shipping, b_link, b_title = fetch_baemin_price(product_name, spec)

            # 3. 최저가 채널 결정 (더 저렴한 가격 선택)
            final_channel, final_price, final_shipping, final_link = d_channel, d_price, d_shipping, d_link

            if b_price > 0 and (d_price == 0 or b_price < d_price):
                print(f"  💡 [배민상회 우세] 배민({b_price:,}원) < 다나와({d_price:,}원)")
                final_channel, final_price, final_shipping, final_link = b_channel, b_price, b_shipping, b_link
            elif d_price > 0:
                print(f"  ⚖️ [다나와 우세/동일] 다나와({d_price:,}원) <= 배민({b_price:,}원)")

            if final_price == 0:
                final_channel, final_price, final_shipping, final_link = "검색결과없음", 0, "", "-"

            # 구글 시트 업데이트
            worksheet.update_cell(row_idx, 10, final_channel)
            worksheet.update_cell(row_idx, 11, final_price)
            worksheet.update_cell(row_idx, 12, final_shipping)

            if final_link and final_link != "-":
                hyperlink_formula = f'=HYPERLINK("{final_link}", "링크보기")'
                worksheet.update_cell(row_idx, 19, hyperlink_formula)
            else:
                worksheet.update_cell(row_idx, 19, "-")

            disp_shipping = final_shipping if final_shipping else "공란(무료/없음)"
            print(f"  ✔ [완료] 최종채널={final_channel} | 최저가={final_price:,}원 | 배송비={disp_shipping}\n")

            time.sleep(2.0)

            if row_idx % 50 == 0:
                print("  💤 API 요청 안정화를 위해 5초간 대기합니다...\n")
                time.sleep(5)

        print("🎉 Gemini AI 정밀 검증 기반 최저가 조사가 완료되었습니다!")

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        raise e

if __name__ == "__main__":
    run_price_update()
