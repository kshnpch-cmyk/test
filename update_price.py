import os
import json
import time
import re
import warnings
import gspread
from google import genai
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from bs4 import BeautifulSoup
from curl_cffi import requests
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# 경고 문구 출력 차단
warnings.filterwarnings("ignore", category=UserWarning)

gemini_api_key = os.environ.get('GEMINI_API_KEY')
ai_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None

@retry(
    retry=retry_if_exception_type(gspread.exceptions.APIError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=3, max=30)
)
def safe_open_sheet(gc, sheet_url):
    return gc.open_by_url(sheet_url)

@retry(
    retry=retry_if_exception_type(gspread.exceptions.APIError),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=2, min=3, max=30)
)
def safe_batch_update(worksheet, cell_range, values_matrix):
    worksheet.update(cell_range, values_matrix, raw=False)

@retry(
    retry=retry_if_exception_type(Exception),
    stop=stop_after_attempt(5),
    wait=wait_exponential(multiplier=3, min=8, max=30)
)
def call_gemini_api(prompt):
    return ai_client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config={
            'response_mime_type': 'application/json',
            'tools': []
        }
    )

def analyze_product_with_gemini(sheet_product, sheet_spec, crawled_title, crawled_price, raw_shipping_text=""):
    if not ai_client:
        return crawled_price, True, ""

    prompt = f"""
너는 쇼핑몰 데이터 분석 및 용량 계산 전문가야.
아래 [구글 시트 요청 정보]와 크롤링한 [쇼핑몰 검색 결과]를 분석해줘.

[구글 시트 요청 정보]
- 품목명: {sheet_product}
- 시트 규격: {sheet_spec}

[쇼핑몰 검색 결과]
- 검색된 상품명: {crawled_title}
- 검색된 표시 가격: {crawled_price}원
- 수집된 배송비 관련 텍스트: {raw_shipping_text}

다음 질문에 맞춰 오직 JSON 형식으로만 응답해:
1. "is_matched": 검색된 상품이 시트 요청 품목과 동일한 종류인지 여부 (true/false)
2. "crawled_total_capacity_g": 검색된 상품명의 전체 총 용량/중량(g 또는 ml 숫자만, 예: 12kg -> 12000). 모르면 0
3. "sheet_target_capacity_g": 구글 시트 규격의 전체 목표 총 용량/중량(g 또는 ml 숫자만, 예: 10kg -> 10000). 모르면 0
4. "shipping_fee": 배송비 금액 (무료/로켓배송/정보없음은 "", 유료배송비면 '3,000원' 형태)
5. "reason": 판단 및 중량 환산 이유 요약

응답 형식(JSON):
{{
  "is_matched": true,
  "crawled_total_capacity_g": 12000,
  "sheet_target_capacity_g": 10000,
  "shipping_fee": "",
  "reason": "검색 제품은 총 12kg(51,020원)이며 시트 목표는 10kg이므로 비례 환산 적용 대상입니다."
}}
"""

    try:
        response = call_gemini_api(prompt)
        time.sleep(1.2)
        
        result = json.loads(response.text)
        is_matched = result.get("is_matched", True)
        crawled_g = result.get("crawled_total_capacity_g", 0)
        target_g = result.get("sheet_target_capacity_g", 0)
        shipping_fee = result.get("shipping_fee", "")
        reason = result.get("reason", "")

        final_calculated_price = crawled_price
        if crawled_g > 0 and target_g > 0:
            final_calculated_price = int((crawled_price / crawled_g) * target_g)
            print(f"  🤖 [Gemini 환산] {crawled_g}g({crawled_price:,}원) ➔ {target_g}g 환산가: {final_calculated_price:,}원", flush=True)
        else:
            print(f"  🤖 [Gemini 분석] {reason}", flush=True)

        return final_calculated_price, is_matched, shipping_fee

    except Exception as e:
        print(f"  ❌ Gemini 분석 예외: {e}", flush=True)
        return crawled_price, True, ""

def fetch_direct_url_price(target_url, product_name, spec):
    """📌 T열에 지정된 URL 페이지에 직접 접속하여 가격/배송비를 파싱합니다."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://www.google.com/"
    }

    try:
        response = requests.get(target_url, headers=headers, impersonate="chrome120", timeout=12)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 제목 파싱
            title_el = (
                soup.select_one("h3.prod_tit") or 
                soup.select_one("h2.prod_title") or 
                soup.select_one("h1") or 
                soup.select_one("title")
            )
            title_text = title_el.text.strip() if title_el else product_name

            # 가격 파싱 (다나와/쿠팡/네이버/일반 쇼핑몰 공통 정규식)
            price_el = (
                soup.select_one("span.prc_c") or 
                soup.select_one("strong.price-value") or 
                soup.select_one("span.price_num") or 
                soup.select_one("em.num")
            )
            
            raw_price = 0
            if price_el:
                nums = re.findall(r'[\d,]+', price_el.text)
                if nums:
                    raw_price = int(nums[0].replace(",", ""))

            if raw_price == 0:
                # 전체 HTML 텍스트에서 금액 패턴 스캔
                matches = re.findall(r'(?:판매가|최저가|가격)\s*:?\s*([\d,]+)\s*원', response.text)
                if matches:
                    raw_price = int(matches[0].replace(",", ""))

            # 배송비 파싱
            ship_matches = re.findall(r'(?:배송비|택배비)\s*:?\s*([\d,]+)\s*원', response.text)
            ship_text = f"배송비: {ship_matches[0]}원" if ship_matches else ""

            if raw_price > 0:
                calculated_price, is_matched, ai_shipping = analyze_product_with_gemini(
                    product_name, spec, title_text, raw_price, raw_shipping_text=ship_text
                )
                
                channel_name = "지정URL"
                if "danawa.com" in target_url: channel_name = "다나와(지정)"
                elif "coupang.com" in target_url: channel_name = "쿠팡(지정)"
                elif "naver.com" in target_url: channel_name = "네이버(지정)"
                elif "baemin.com" in target_url: channel_name = "배민상회(지정)"

                return channel_name, calculated_price, ai_shipping, target_url

    except Exception as e:
        print(f"  ❌ 지정 URL 수집 예외: {e}", flush=True)

    return "지정URL오류", 0, "", target_url

# ----------------------------------------------------
# 기존 4대 채널 수집 함수 (다나와 / 배민 / 네이버 / 쿠팡)
# ----------------------------------------------------
def extract_pack_quantity(spec_text):
    if not spec_text: return 1
    match = re.search(r'\*\s*(\d+)\s*(?:ea|개|팩|box|박스|통|병|개입)?', spec_text, re.IGNORECASE)
    if match: return int(match.group(1))
    match_unit = re.search(r'(\d+)\s*(?:ea|개|팩|box|박스|통|병)(?!\w)', spec_text, re.IGNORECASE)
    if match_unit: return int(match_unit.group(1))
    return 1

def fetch_danawa_price(product_name, spec):
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    search_query = clean_p if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE) else f"{clean_p} {clean_s}".strip()
    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://search.danawa.com/dsearch.php?k1={encoded_query}&module=goods&act=dispMain"
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://www.danawa.com/"}
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
                ship_el = first_prod.select_one("span.ship_fee") or first_prod.select_one("td.ship") or first_prod.select_one("span.stxt")
                ship_text = ship_el.text.strip() if ship_el else ""

                if raw_price > 0:
                    calculated_price, is_matched, ai_shipping = analyze_product_with_gemini(product_name, spec, title_text, raw_price, ship_text)
                    if is_matched:
                        channel_el = first_prod.select_one("div.memory_sect p.memory_mall") or first_prod.select_one("p.mall_name")
                        mall_text = channel_el.text.strip() if (channel_el and channel_el.text.strip()) else "다나와"
                        link_el = first_prod.select_one("p.prod_name a") or first_prod.select_one("a.thumb_link")
                        href = link_el.get('href') if link_el else ""
                        product_link = f"https:{href}" if href.startswith("//") else (href or search_url)
                        return mall_text, calculated_price, ai_shipping, product_link
    except: pass
    return "다나와", 0, "", "-"

def fetch_baemin_price(product_name, spec):
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    search_query = clean_p if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE) else f"{clean_p} {clean_s}".strip()
    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://mart.baemin.com/search?keyword={encoded_query}"
    
    headers = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X)", "Referer": "https://mart.baemin.com/"}
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
                        calculated_price, is_matched, ai_shipping = analyze_product_with_gemini(product_name, spec, title_text, raw_price, first_prod.text[:200])
                        if is_matched:
                            href = first_prod.get('href', '')
                            product_link = f"https://mart.baemin.com{href}" if href.startswith('/') else search_url
                            return "배민상회", calculated_price, ai_shipping, product_link
    except: pass
    return "배민상회", 0, "", "-"

def fetch_naver_price(product_name, spec):
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    search_query = clean_p if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE) else f"{clean_p} {clean_s}".strip()
    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://search.shopping.naver.com/search/all?query={encoded_query}"
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://shopping.naver.com/"}
    try:
        response = requests.get(search_url, headers=headers, impersonate="chrome120", timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            products = soup.select("div[class*='product_item']") or soup.select("li[class*='basicList_item']")
            if products:
                first_prod = products[0]
                title_el = first_prod.select_one("a[class*='product_link']") or first_prod.select_one("a[title]")
                title_text = title_el.text.strip() if title_el else ""
                price_el = first_prod.select_one("span[class*='price_num']") or first_prod.select_one("em[class*='num']")
                raw_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0
                ship_el = first_prod.select_one("span[class*='price_delivery']") or first_prod.select_one("div[class*='delivery']")
                ship_text = ship_el.text.strip() if ship_el else ""

                if raw_price > 0:
                    calculated_price, is_matched, ai_shipping = analyze_product_with_gemini(product_name, spec, title_text, raw_price, ship_text)
                    if is_matched:
                        href = title_el.get('href', '') if title_el else ""
                        product_link = href if href.startswith("http") else search_url
                        return "네이버쇼핑", calculated_price, ai_shipping, product_link
    except: pass
    return "네이버쇼핑", 0, "", "-"

def fetch_coupang_price(product_name, spec):
    clean_p = product_name.replace("*", " ").strip()
    clean_s = spec.replace("*", " ").strip()
    search_query = clean_p if re.search(r'\d+\s*(g|kg|l|ml|ea)', clean_p, re.IGNORECASE) else f"{clean_p} {clean_s}".strip()
    encoded_query = requests.utils.quote(search_query)
    search_url = f"https://www.coupang.com/np/search?q={encoded_query}"
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://www.coupang.com/"}
    try:
        response = requests.get(search_url, headers=headers, impersonate="chrome120", timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            products = soup.select("li.search-product")
            if products:
                first_prod = products[0]
                for prod in products:
                    if not prod.select_one("span.ad-badge"):
                        first_prod = prod
                        break
                title_el = first_prod.select_one("div.name")
                title_text = title_el.text.strip() if title_el else ""
                price_el = first_prod.select_one("strong.price-value")
                raw_price = int(re.sub(r'[^\d]', '', price_el.text)) if price_el else 0
                delivery_el = first_prod.select_one("span.delivery-badge") or first_prod.select_one("div.delivery")
                delivery_text = delivery_el.text.strip() if delivery_el else ""

                if raw_price > 0:
                    calculated_price, is_matched, ai_shipping = analyze_product_with_gemini(product_name, spec, title_text, raw_price, delivery_text)
                    if is_matched:
                        href = first_prod.select_one("a").get('href', '') if first_prod.select_one("a") else ""
                        product_link = f"https://www.coupang.com{href}" if href.startswith('/') else search_url
                        return "쿠팡", calculated_price, ai_shipping, product_link
    except: pass
    return "쿠팡", 0, "", "-"

# ----------------------------------------------------
# 메인 실행 로직
# ----------------------------------------------------
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
        
        doc = safe_open_sheet(gc, sheet_url)
        worksheet = doc.get_worksheet(0)

        all_rows = worksheet.get_all_values()
        total_rows = len(all_rows)

        print("==================================================", flush=True)
        print(f"🚀 [T열 URL 우선 지원 / 4채널 수집 가동] 총 {total_rows}개 행 조사를 시작합니다.", flush=True)
        print("==================================================\n", flush=True)

        batch_data = []

        for row_idx in range(3, total_rows + 1):
            row_values = all_rows[row_idx - 1] if (row_idx - 1) < len(all_rows) else []

            orig_j_to_s = row_values[9:19] if len(row_values) >= 19 else [""] * 10
            while len(orig_j_to_s) < 10:
                orig_j_to_s.append("")

            product_name = row_values[2] if len(row_values) >= 3 else ""
            spec = row_values[3] if len(row_values) >= 4 else ""
            category = row_values[8] if len(row_values) >= 9 else ""
            custom_url = row_values[19].strip() if len(row_values) >= 20 else ""  # T열 (인덱스 19)

            print(f"▶ [{row_idx}/{total_rows}행] 품목: '{product_name}' | 규격: '{spec}' | T열URL: '{custom_url[:30]}...'", flush=True)

            if any(skip_word in category for skip_word in ['전용', '예산', '종료']) or not product_name.strip():
                print(f"  ⏭️ 스킵됨", flush=True)
                batch_data.append(orig_j_to_s)
                continue

            # 📌 1. T열에 direct URL이 있으면 우선적으로 해당 페이지에서 가져옴
            if custom_url.startswith("http"):
                print(f"  🔗 [T열 지정 링크 직진] {custom_url}", flush=True)
                best_channel, best_price, best_shipping, best_link = fetch_direct_url_price(custom_url, product_name, spec)
            else:
                # 📌 2. T열이 비어있으면 4개 채널 자동 검색
                results = [
                    fetch_danawa_price(product_name, spec),
                    fetch_baemin_price(product_name, spec),
                    fetch_naver_price(product_name, spec),
                    fetch_coupang_price(product_name, spec)
                ]

                valid_results = [r for r in results if r[1] > 0]

                if valid_results:
                    valid_results.sort(key=lambda x: x[1])
                    best_channel, best_price, best_shipping, best_link = valid_results[0]
                    print(f"  🏆 [최저가 확정] 채널: {best_channel} | 최종 환산가: {best_price:,}원", flush=True)
                else:
                    best_channel, best_price, best_shipping, best_link = "검색결과없음", 0, "", "-"
                    print(f"  ⚠️ [검색 결과 없음] 4개 채널에서 모두 제품을 찾지 못했습니다.", flush=True)

            link_formula = f'=HYPERLINK("{best_link}", "링크보기")' if (best_link and best_link != "-") else "-"

            row_update = list(orig_j_to_s)
            row_update[0] = best_channel
            row_update[1] = best_price
            row_update[2] = best_shipping
            row_update[9] = link_formula

            batch_data.append(row_update)
            time.sleep(0.5)

        print("\n📤 [시트 반영 중] 수집된 전체 최저가 데이터를 구글 시트에 일괄 기록합니다...", flush=True)
        cell_range = f"J3:S{total_rows}"
        safe_batch_update(worksheet, cell_range, batch_data)

        print("🎉 T열 URL 우선 지원 및 시트 일괄 업데이트가 완벽히 완료되었습니다!", flush=True)

    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}", flush=True)
        raise e

if __name__ == "__main__":
    run_price_update()
