import os
import json
import time
import re
import warnings
import io
import base64
from urllib.parse import quote_plus

import gspread
from google import genai
from anthropic import Anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.sync_api import sync_playwright
from sentence_transformers import SentenceTransformer, util
from PIL import Image

# ============================================================
# 기본 설정
# ============================================================

warnings.filterwarnings("ignore", category=UserWarning)

print("📦 BERT 모델(ko-sbert-sts) 로딩 중...", flush=True)
bert_model = SentenceTransformer("jhgan/ko-sbert-sts")

# ============================================================
# API 클라이언트
# ============================================================

gemini_api_key = os.environ.get("GEMINI_API_KEY")
anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

gemini_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None
claude_client = Anthropic(api_key=anthropic_api_key) if anthropic_api_key else None

# ============================================================
# Google Sheet 안전 처리
# ============================================================

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

# ============================================================
# 숫자 / 문자열 유틸
# ============================================================

def normalize_text(text):
    if not text:
        return ""
    text = str(text).lower()
    text = re.sub(r"[\(\)\[\]\{\},./:_\-+×*|]", " ", text)
    text = re.sub(r"[^0-9a-zA-Z가-힣\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def parse_shipping_fee(text):
    if not text:
        return 0, "배송비 정보없음"

    raw = str(text).strip()
    normalized = raw.replace(" ", "").lower()
    free_words = ["무료", "무료배송", "로켓", "로켓배송", "당일무료", "조건부무료"]

    if any(word in normalized for word in free_words):
        return 0, "무료배송"

    matches = re.findall(r"(\d[\d,]*)\s*원", raw)
    if matches:
        values = []
        for value in matches:
            try:
                values.append(int(value.replace(",", "")))
            except Exception:
                pass
        if values:
            fee = min(values)
            return fee, f"{fee:,}원"

    number_matches = re.findall(r"(?<!\d)(\d[\d,]*)(?!\d)", raw)
    if number_matches:
        values = []
        for value in number_matches:
            try:
                number = int(value.replace(",", ""))
                if 0 < number <= 100000:
                    values.append(number)
            except Exception:
                pass
        if values:
            fee = min(values)
            return fee, f"{fee:,}원"

    return 0, raw[:30]

# ============================================================
# 용량 / 수량 분석
# ============================================================

def normalize_units(text):
    if not text:
        return ""
    text = str(text).lower()
    replacements = {
        "킬로그램": "kg", "킬로": "kg", "그램": "g",
        "리터": "l", "밀리리터": "ml", "온스": "oz",
        "ounce": "oz", "ounces": "oz", "개입": "개", "입": "개"
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text

def parse_quantity_info(text):
    if not text:
        return {"amount": 0, "unit": "", "quantity": 1, "total": 0, "dimension": ""}

    text = normalize_units(text).lower()
    amount, unit, quantity = 0.0, "", 1

    pattern1 = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|l|ml|oz)\s*(?:x|×|\*|\s)\s*(\d+)\s*(?:개|입|팩|병|캔|ea)?", text)
    if pattern1:
        amount = float(pattern1.group(1))
        unit = pattern1.group(2)
        quantity = int(pattern1.group(3))
    else:
        pattern2 = re.search(r"(\d+)\s*(?:개|입|팩|병|캔|ea)\s*(\d+(?:\.\d+)?)\s*(kg|g|l|ml|oz)", text)
        if pattern2:
            quantity = int(pattern2.group(1))
            amount = float(pattern2.group(2))
            unit = pattern2.group(3)
        else:
            pattern3 = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|l|ml|oz)", text)
            if pattern3:
                amount = float(pattern3.group(1))
                unit = pattern3.group(2)

            quantity_match = re.search(r"(\d+)\s*(?:개|입|팩|병|캔|ea)", text)
            if quantity_match:
                quantity = int(quantity_match.group(1))

    if unit == "oz":
        total = amount * 29.5735 * quantity
        dimension = "volume"
    elif unit in ["kg", "g"]:
        multiplier = 1000 if unit == "kg" else 1
        total = amount * multiplier * quantity
        dimension = "weight"
    elif unit in ["l", "ml"]:
        multiplier = 1000 if unit == "l" else 1
        total = amount * multiplier * quantity
        dimension = "volume"
    else:
        total = 0
        dimension = ""

    return {
        "amount": amount,
        "unit": unit,
        "quantity": quantity,
        "total": int(round(total)),
        "dimension": dimension
    }

# ============================================================
# 검색어 정리 (T열 수동 지정 키워드 우회 복원)
# ============================================================

def clean_search_keyword(product_name, spec, custom_val=""):
    """T열 입력값이 있으면 최우선 검색어로 채택"""
    if custom_val and not custom_val.startswith("http"):
        return custom_val.strip()

    product_name = product_name or ""
    spec = spec or ""

    clean_product = normalize_text(product_name)
    clean_spec = normalize_text(spec)

    info = parse_quantity_info(spec)
    parts = []

    if clean_product:
        parts.append(clean_product)

    if info["amount"] > 0 and info["unit"]:
        amount_text = str(info["amount"]).rstrip("0").rstrip(".")
        parts.append(f"{amount_text}{info['unit']}")

    if info["quantity"] > 1:
        parts.append(f"{info['quantity']}개")

    if len(parts) == 1 and clean_spec:
        parts.append(clean_spec)

    return re.sub(r"\s+", " ", " ".join(parts)).strip()

# ============================================================
# SBERT 유사도
# ============================================================

def calculate_bert_similarity(query, candidate_title):
    try:
        emb1 = bert_model.encode(query, convert_to_tensor=True)
        emb2 = bert_model.encode(candidate_title, convert_to_tensor=True)
        return float(util.cos_sim(emb1, emb2).item())
    except Exception as e:
        print(f"  ⚠️ SBERT 오류: {e}", flush=True)
        return 0.0

def extract_core_tokens(text):
    text = normalize_text(text)
    tokens = text.split()
    stopwords = {"상품", "무료", "배송", "특가", "당일", "판매", "정품", "세트", "대용량", "최저가", "행사", "추천"}
    return [token for token in tokens if token not in stopwords and len(token) >= 2]

def basic_product_match(sheet_product, sheet_spec, crawled_title):
    sheet_text = normalize_text(f"{sheet_product} {sheet_spec}")
    crawled_text = normalize_text(crawled_title)

    if not sheet_text or not crawled_text:
        return False

    sheet_info = parse_quantity_info(sheet_text)
    crawled_info = parse_quantity_info(crawled_text)

    if (sheet_info["total"] > 0 and crawled_info["total"] > 0 and 
        sheet_info["dimension"] and crawled_info["dimension"]):
        if sheet_info["dimension"] != crawled_info["dimension"]:
            return False
        ratio = crawled_info["total"] / sheet_info["total"]
        if ratio < 0.20 or ratio > 5:
            return False

    sheet_tokens = extract_core_tokens(sheet_product)
    if not sheet_tokens:
        return True

    matched = sum(1 for token in sheet_tokens if token in crawled_text)
    token_ratio = matched / max(1, min(len(sheet_tokens), 5))

    if len(sheet_tokens) >= 2 and token_ratio < 0.15:
        return False

    return True

# ============================================================
# Gemini / Claude 텍스트 AI (모델명 정정 완료)
# ============================================================

def call_ai_text_fallback(prompt):
    # 1차 Gemini
    if gemini_client:
        try:
            res = gemini_client.models.generate_content(
                model="gemini-3.6-flash",
                contents=prompt,
                config={"response_mime_type": "application/json", "tools": []}
            )
            time.sleep(1.2)
            if res and res.text:
                return res.text
        except Exception as e:
            print(f"  ⚠️ [Gemini 실패] {e} → Claude 전환 시도", flush=True)

    # 2차 Claude (정확한 모델명 사용)
    if claude_client:
        try:
            res = claude_client.messages.create(
                model="claude-3-5-haiku-latest",
                max_tokens=800,
                system="너는 쇼핑몰 상품 데이터 분석 전문가다. 반드시 순수 JSON만 반환한다.",
                messages=[{"role": "user", "content": prompt}]
            )
            time.sleep(1.0)
            if res and res.content:
                print("  🟣 [Claude 3.5 Haiku 성공]", flush=True)
                return res.content[0].text
        except Exception as e:
            print(f"  ❌ [Claude 실패]: {e}", flush=True)

    return None

# ============================================================
# Vision OCR (모델명 정정 완료)
# ============================================================

def extract_price_via_vision_ocr(image_bytes):
    prompt = """
쇼핑몰 화면 캡처에서 실제 판매 가격과 배송비를 찾아라.
반드시 아래 JSON 형식만 반환한다:
{"price": 51020, "shipping_fee": "무료", "shipping_fee_number": 0}
"""
    # 1차 Gemini Vision
    if gemini_client:
        try:
            image = Image.open(io.BytesIO(image_bytes))
            res = gemini_client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[image, prompt],
                config={"response_mime_type": "application/json", "tools": []}
            )
            result = json.loads(res.text)
            return (
                int(result.get("price", 0) or 0),
                str(result.get("shipping_fee", "") or ""),
                int(result.get("shipping_fee_number", 0) or 0)
            )
        except Exception as e:
            print(f"  ⚠️ [Gemini Vision 실패] {e} ➔ Claude Vision 전환 시도", flush=True)

    # 2차 Claude Vision
    if claude_client:
        try:
            base64_image = base64.b64encode(image_bytes).decode("utf-8")
            res = claude_client.messages.create(
                model="claude-3-5-sonnet-latest",
                max_tokens=500,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64_image}},
                        {"type": "text", "text": prompt}
                    ]
                }]
            )
            result = json.loads(res.content[0].text)
            print("  🟣 [Claude 3.5 Sonnet Vision 성공]", flush=True)
            return (
                int(result.get("price", 0) or 0),
                str(result.get("shipping_fee", "") or ""),
                int(result.get("shipping_fee_number", 0) or 0)
            )
        except Exception as e:
            print(f"  ❌ [Claude Vision 실패]: {e}", flush=True)

    return 0, "", 0

# ============================================================
# AI 상품 스마트 분석
# ============================================================

def analyze_product_smart(sheet_product, sheet_spec, crawled_title, crawled_price, raw_shipping_text="", bert_score=0.0):
    if crawled_price <= 0:
        return {"price": 0, "shipping_fee": 0, "shipping_text": "", "total_price": 0, "matched": False, "reason": "가격정보 없음"}

    if not basic_product_match(sheet_product, sheet_spec, crawled_title):
        return {"price": crawled_price, "shipping_fee": 0, "shipping_text": "", "total_price": 0, "matched": False, "reason": "기본 검증 실패"}

    shipping_fee, shipping_text = parse_shipping_fee(raw_shipping_text)
    sheet_info = parse_quantity_info(f"{sheet_product} {sheet_spec}")
    crawled_info = parse_quantity_info(crawled_title)

    # 고득점자 Fast-pass
    if bert_score >= 0.85:
        if sheet_info["total"] > 0 and crawled_info["total"] > 0 and sheet_info["dimension"] == crawled_info["dimension"]:
            ratio = crawled_info["total"] / sheet_info["total"]
            if 0.85 <= ratio <= 1.15:
                return {
                    "price": crawled_price,
                    "shipping_fee": shipping_fee,
                    "shipping_text": shipping_text,
                    "total_price": crawled_price + shipping_fee,
                    "matched": True,
                    "reason": f"SBERT({bert_score:.2f}) + 규격 일치"
                }

    prompt = f"""
너는 쇼핑몰 가격비교 분석 전문가다. 아래 두 상품을 비교하라.
[시트] 품목:{sheet_product} | 규격:{sheet_spec}
[검색] 상품명:{crawled_title} | 가격:{crawled_price}원 | 배송비:{raw_shipping_text}

반드시 순수 JSON만 반환하라:
{{
  "is_matched": true,
  "crawled_amount": 500, "crawled_unit": "ml", "crawled_quantity": 1,
  "target_amount": 500, "target_unit": "ml", "target_quantity": 1,
  "shipping_fee": 3000, "shipping_text": "3,000원",
  "calculation_reason": "동일 규격 제품 환산 단가 적용"
}}
"""
    response_text = call_ai_text_fallback(prompt)
    if not response_text:
        return {"price": crawled_price, "shipping_fee": shipping_fee, "shipping_text": shipping_text, "total_price": crawled_price + shipping_fee, "matched": False, "reason": "AI 검증 실패"}

    try:
        clean_json = re.sub(r"```json\s*|\s*```", "", response_text).strip()
        result = json.loads(clean_json)

        if not result.get("is_matched", False):
            return {"price": crawled_price, "shipping_fee": 0, "shipping_text": "", "total_price": 0, "matched": False, "reason": result.get("calculation_reason", "AI 불일치 판정")}

        cg, cq = float(result.get("crawled_amount", 0) or 0), int(result.get("crawled_quantity", 1) or 1)
        tg, tq = float(result.get("target_amount", 0) or 0), int(result.get("target_quantity", 1) or 1)

        final_price = crawled_price
        crawled_total, target_total = cg * cq, tg * tq

        if crawled_total > 0 and target_total > 0:
            final_price = int(round(crawled_price * (target_total / crawled_total)))

        ai_ship_fee = int(result.get("shipping_fee", shipping_fee) or 0)
        ai_ship_text = str(result.get("shipping_text", shipping_text) or "")

        return {
            "price": final_price,
            "shipping_fee": ai_ship_fee,
            "shipping_text": ai_ship_text,
            "total_price": final_price + ai_ship_fee,
            "matched": True,
            "reason": result.get("calculation_reason", "AI 분석 완료")
        }
    except Exception as e:
        return {"price": crawled_price, "shipping_fee": shipping_fee, "shipping_text": shipping_text, "total_price": crawled_price + shipping_fee, "matched": False, "reason": f"JSON 오류: {e}"}

# ============================================================
# 크롤링 파서 (다나와, 네이버, 쿠팡)
# ============================================================

def parse_danawa(items, search_url):
    results = []
    for product in items[:5]:
        title_el = product.select_one("p.prod_name a")
        title_text = title_el.text.strip() if title_el else ""
        price_el = product.select_one("p.price_sect a strong") or product.select_one("span.num")
        raw_price = int(re.sub(r"[^\d]", "", price_el.text)) if price_el and re.sub(r"[^\d]", "", price_el.text) else 0
        ship_el = product.select_one("span.ship_fee") or product.select_one("td.ship") or product.select_one("span.stxt")
        ship_text = ship_el.text.strip() if ship_el else ""

        if raw_price > 0 and title_text:
            channel_el = product.select_one("div.memory_sect p.memory_mall") or product.select_one("p.mall_name")
            mall_text = channel_el.text.strip() if channel_el and channel_el.text.strip() else "다나와"
            link_el = product.select_one("p.prod_name a") or product.select_one("a.thumb_link")
            href = link_el.get("href") if link_el else ""
            product_link = f"https:{href}" if href.startswith("//") else (href if href.startswith("http") else search_url)
            results.append((mall_text, title_text, raw_price, ship_text, product_link))
    return results

def parse_naver(items, search_url):
    results = []
    for product in items[:5]:
        title_el = product.select_one("a[class*='product_link']") or product.select_one("a[title]")
        title_text = title_el.text.strip() if title_el else ""
        price_el = product.select_one("span[class*='price_num']") or product.select_one("em[class*='num']")
        raw_price = int(re.sub(r"[^\d]", "", price_el.text)) if price_el and re.sub(r"[^\d]", "", price_el.text) else 0
        ship_el = product.select_one("span[class*='price_delivery']") or product.select_one("div[class*='delivery']")
        ship_text = ship_el.text.strip() if ship_el else ""

        if raw_price > 0 and title_text:
            href = title_el.get("href", "") if title_el else ""
            results.append(("네이버쇼핑", title_text, raw_price, ship_text, href if href.startswith("http") else search_url))
    return results

def parse_coupang(items, search_url):
    results = []
    for product in items[:5]:
        if product.select_one("span.ad-badge"): continue
        title_el = product.select_one("div.name")
        title_text = title_el.text.strip() if title_el else ""
        price_el = product.select_one("strong.price-value")
        raw_price = int(re.sub(r"[^\d]", "", price_el.text)) if price_el and re.sub(r"[^\d]", "", price_el.text) else 0
        delivery_el = product.select_one("span.delivery-badge") or product.select_one("div.delivery")
        delivery_text = delivery_el.text.strip() if delivery_el else ""

        if raw_price > 0 and title_text:
            link_el = product.select_one("a")
            href = link_el.get("href", "") if link_el else ""
            product_link = f"https://www.coupang.com{href}" if href.startswith("/") else (href if href.startswith("http") else search_url)
            results.append(("쿠팡", title_text, raw_price, delivery_text, product_link))
    return results

def fetch_with_browser_and_ocr(page, url, selector_item, parser_fn):
    try:
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        time.sleep(1.5)
        soup = BeautifulSoup(page.content(), "html.parser")
        items = soup.select(selector_item)
        results = parser_fn(items, url)

        if not results:
            screenshot_bytes = page.screenshot(full_page=False)
            ocr_price, ocr_ship_text, _ = extract_price_via_vision_ocr(screenshot_bytes)
            if ocr_price > 0:
                print(f"  📸 [Vision OCR] 감지 가격: {ocr_price:,}원", flush=True)
                results.append(("OCR감지", "화면캡처 상품", ocr_price, ocr_ship_text, url))
        return results
    except Exception as e:
        print(f"  ⚠️ 브라우저 수집 예외: {e}", flush=True)
        return []

# ============================================================
# 메인 프로세스
# ============================================================

def run_price_update():
    try:
        token_secret = os.environ.get("GCP_TOKEN_JSON")
        if not token_secret:
            raise ValueError("❌ GCP_TOKEN_JSON 설정이 필요합니다.")

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
        print(f"🚀 총 {total_rows}개 행 최저가 수집 및 AI 검증 가동", flush=True)
        print("==================================================\n", flush=True)

        batch_data = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                locale="ko-KR", viewport={"width": 1440, "height": 1000}
            )
            page = context.new_page()

            for row_idx in range(3, total_rows + 1):
                try:
                    row_values = all_rows[row_idx - 1] if row_idx - 1 < len(all_rows) else []
                    orig_j_to_s = row_values[9:19] if len(row_values) >= 19 else [""] * 10
                    while len(orig_j_to_s) < 10: orig_j_to_s.append("")

                    product_name = row_values[2] if len(row_values) >= 3 else ""
                    spec = row_values[3] if len(row_values) >= 4 else ""
                    category = row_values[8] if len(row_values) >= 9 else ""
                    custom_val = row_values[19].strip() if len(row_values) >= 20 else ""

                    print(f"▶ [{row_idx}/{total_rows}] {product_name} | 규격: {spec}", flush=True)

                    if any(skip_word in category for skip_word in ["전용", "예산", "종료"]) or not product_name.strip():
                        print("  ⏭️ 스킵", flush=True)
                        batch_data.append(orig_j_to_s)
                        continue

                    # T열 키워드 우선 사용
                    search_query = clean_search_keyword(product_name, spec, custom_val)
                    encoded_query = quote_plus(search_query)

                    targets = [
                        ("다나와", f"https://search.danawa.com/dsearch.php?k1={encoded_query}&module=goods&act=dispMain", "li.prod_item:not(.product-pot)", parse_danawa),
                        ("네이버", f"https://search.shopping.naver.com/search/all?query={encoded_query}", "div[class*='product_item'], li[class*='basicList_item']", parse_naver),
                        ("쿠팡", f"https://www.coupang.com/np/search?q={encoded_query}", "li.search-product", parse_coupang)
                    ]

                    valid_results = []
                    for site_name, url, selector, parser in targets:
                        candidates = fetch_with_browser_and_ocr(page, url, selector, parser)
                        for mall_name, title, price, ship, link in candidates:
                            if price <= 0: continue
                            sim_score = calculate_bert_similarity(search_query, title)
                            
                            # 컷오프 0.42로 완화
                            if sim_score < 0.42: continue

                            analysis = analyze_product_smart(product_name, spec, title, price, ship, bert_score=sim_score)
                            if analysis["matched"] and analysis["total_price"] > 0:
                                valid_results.append({
                                    "mall": mall_name, "title": title, "price": analysis["price"],
                                    "shipping_fee": analysis["shipping_fee"], "shipping_text": analysis["shipping_text"],
                                    "total_price": analysis["total_price"], "link": link, "reason": analysis["reason"],
                                    "bert_score": sim_score
                                })

                    if valid_results:
                        valid_results.sort(key=lambda x: (x["total_price"], -x["bert_score"]))
                        best = valid_results[0]
                        print(f"  🏆 최저가 확정: [{best['mall']}] {best['price']:,}원 (배송: {best['shipping_text']}) | 사유: {best['reason']}", flush=True)

                        safe_link = best["link"].replace('"', '""')
                        link_formula = f'=HYPERLINK("{safe_link}", "링크보기")' if best["link"] != "-" else "-"

                        row_update = list(orig_j_to_s)
                        row_update[0] = best["mall"]           # J열
                        row_update[1] = best["price"]          # K열
                        row_update[2] = best["shipping_text"]  # L열
                        row_update[3] = best["total_price"]    # M열
                        row_update[4] = link_formula           # N열

                        if len(row_update) < 11:
                            while len(row_update) < 11: row_update.append("")
                        row_update[10] = best["reason"]        # T열 (인덱스 10)
                        
                        batch_data.append(row_update)
                    else:
                        print("  ⚠️ 유효 최저가 검색 실패", flush=True)
                        row_update = list(orig_j_to_s)
                        row_update[0] = "검색결과없음"
                        row_update[1] = 0
                        row_update[2] = ""
                        row_update[3] = 0
                        row_update[4] = "-"
                        if len(row_update) < 11:
                            while len(row_update) < 11: row_update.append("")
                        row_update[10] = "유효 동일상품 미발견"
                        batch_data.append(row_update)

                except Exception as row_error:
                    print(f"  ❌ {row_idx}행 처리 에러: {row_error}", flush=True)
                    batch_data.append(orig_j_to_s)

            browser.close()

        print("\n📤 [구글 시트 일괄 업데이트 진행 중...]", flush=True)
        cell_range = f"J3:T{total_rows}"
        safe_batch_update(worksheet, cell_range, batch_data)
        print("🎉 모든 데이터 업데이트가 끝났습니다!", flush=True)

    except Exception as e:
        print(f"❌ 전체 실행 오류: {e}", flush=True)
        raise e

if __name__ == "__main__":
    run_price_update()
