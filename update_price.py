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
from openai import OpenAI
from anthropic import Anthropic
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from playwright.sync_api import sync_playwright
from sentence_transformers import SentenceTransformer, util
from PIL import Image

warnings.filterwarnings("ignore", category=UserWarning)

print("📦 BERT 모델(ko-sbert-sts) 로딩 중...", flush=True)
bert_model = SentenceTransformer("jhgan/ko-sbert-sts")

# API 클라이언트 초기화
gemini_api_key = os.environ.get("GEMINI_API_KEY")
openai_api_key = os.environ.get("OPENAI_API_KEY")
anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY")

gemini_client = genai.Client(api_key=gemini_api_key) if gemini_api_key else None
openai_client = OpenAI(api_key=openai_api_key) if openai_api_key else None
claude_client = Anthropic(api_key=anthropic_api_key) if anthropic_api_key else None

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

def normalize_text(text):
    if not text: return ""
    text = str(text).lower()
    text = re.sub(r"[\(\)\[\]\{\},./:_\-+×*|]", " ", text)
    text = re.sub(r"[^0-9a-zA-Z가-힣\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def parse_shipping_fee(text):
    if not text: return 0, "배송비 정보없음"
    raw = str(text).strip()
    normalized = raw.replace(" ", "").lower()
    free_words = ["무료", "무료배송", "로켓", "로켓배송", "당일무료", "조건부무료"]

    if any(word in normalized for word in free_words):
        return 0, "무료배송"

    matches = re.findall(r"(\d[\d,]*)\s*원", raw)
    if matches:
        values = [int(v.replace(",", "")) for v in matches if v.replace(",", "").isdigit()]
        if values:
            fee = min(values)
            return fee, f"{fee:,}원"

    return 0, raw[:30]

def parse_quantity_info(text):
    if not text: return {"total": 0, "dimension": ""}
    text = str(text).lower().replace("킬로그램", "kg").replace("그램", "g").replace("리터", "l").replace("밀리리터", "ml")
    
    multi_match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|l|ml)\s*(?:x|×|\*|\s)\s*(\d+)", text)
    if multi_match:
        val, unit, qty = float(multi_match.group(1)), multi_match.group(2), int(multi_match.group(3))
        multiplier = 1000 if unit in ["kg", "l"] else 1
        return {"total": int(val * multiplier * qty), "dimension": "weight" if unit in ["kg", "g"] else "volume"}

    single_match = re.search(r"(\d+(?:\.\d+)?)\s*(kg|g|l|ml)", text)
    if single_match:
        val, unit = float(single_match.group(1)), single_match.group(2)
        multiplier = 1000 if unit in ["kg", "l"] else 1
        return {"total": int(val * multiplier), "dimension": "weight" if unit in ["kg", "g"] else "volume"}

    return {"total": 0, "dimension": ""}

def clean_search_keyword(product_name, spec, custom_val=""):
    if custom_val and not custom_val.startswith("http"):
        return custom_val.strip()
    return re.sub(r"\s+", " ", f"{normalize_text(product_name)} {normalize_text(spec)}").strip()

def calculate_bert_similarity(query, candidate_title):
    try:
        emb1 = bert_model.encode(query, convert_to_tensor=True)
        emb2 = bert_model.encode(candidate_title, convert_to_tensor=True)
        return float(util.cos_sim(emb1, emb2).item())
    except Exception:
        return 0.0

def call_ai_text_fallback(prompt):
    # 1차 Gemini
    if gemini_client:
        try:
            res = gemini_client.models.generate_content(
                model="gemini-3.6-flash", contents=prompt,
                config={"response_mime_type": "application/json", "tools": []}
            )
            time.sleep(1.0)
            if res and res.text: return res.text
        except Exception as e:
            print(f"  ⚠️ [Gemini 실패] {e} ➔ GPT 우회", flush=True)

    # 2차 OpenAI GPT
    if openai_client:
        try:
            res = openai_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "너는 데이터 분석가야. 순수 JSON으로만 응답해."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"}
            )
            time.sleep(0.8)
            if res and res.choices[0].message.content:
                print("  🟢 [GPT-4o-mini 응답 성공]", flush=True)
                return res.choices[0].message.content
        except Exception as e:
            print(f"  ⚠️ [GPT API 실패]: {e} ➔ Claude 우회", flush=True)

    # 3차 Claude
    if claude_client:
        try:
            res = claude_client.messages.create(
                model="claude-3-5-haiku-latest", max_tokens=500,
                system="너는 데이터 분석가야. 순수 JSON으로만 응답해.",
                messages=[{"role": "user", "content": prompt}]
            )
            time.sleep(0.8)
            if res and res.content:
                print("  🟣 [Claude 응답 성공]", flush=True)
                return res.content[0].text
        except Exception as e:
            print(f"  ❌ [Claude API 실패]: {e}", flush=True)

    return None

def analyze_product_smart(sheet_product, sheet_spec, crawled_title, crawled_price, raw_shipping_text="", bert_score=0.0):
    """
    📌 핵심: 수집가(crawled_price)를 기본으로 채택하고,
            중량이 명백히 다를 때만 AI를 호출하여 비례 환산함.
    """
    if crawled_price <= 0:
        return {"price": 0, "shipping_fee": 0, "shipping_text": "", "total_price": 0, "matched": False, "reason": "가격정보 없음"}

    shipping_fee, shipping_text = parse_shipping_fee(raw_shipping_text)
    sheet_info = parse_quantity_info(f"{sheet_product} {sheet_spec}")
    crawled_info = parse_quantity_info(crawled_title)

    # 1. ⚡ [AI 호출 0회] 기본 패스: 용량이 완벽히 같거나 정밀도가 높은 경우 AI 스킵
    if (sheet_info["total"] > 0 and crawled_info["total"] > 0 and sheet_info["total"] == crawled_info["total"]) or bert_score >= 0.75:
        print(f"  ⚡ [즉시 채택] 수집가 {crawled_price:,}원 그대로 채택 (AI 호출 스킵)", flush=True)
        return {
            "price": crawled_price,
            "shipping_fee": shipping_fee,
            "shipping_text": shipping_text,
            "total_price": crawled_price + shipping_fee,
            "matched": True,
            "reason": f"수집 단가 적용 (SBERT: {bert_score:.2f})"
        }

    # 2. 🤖 [AI 호출] 중량이 서로 달라서 환산 계산이 필요할 때만 AI 검증
    prompt = f"""
너는 용량 환산 전문가야. 아래 정보를 비교분석해줘.
[시트목표] {sheet_product} {sheet_spec}
[수집상품] {crawled_title} | 가격: {crawled_price}원

오직 JSON만 응답:
{{
  "is_matched": true,
  "crawled_total_g": {crawled_info['total']},
  "target_total_g": {sheet_info['total']},
  "reason": "12kg(51,020원)을 10kg 목표 중량으로 환산하여 책정"
}}
"""
    response_text = call_ai_text_fallback(prompt)
    if not response_text:
        # AI 호출에 실패해도 원본 수집가를 기본으로 채택하여 진행
        return {"price": crawled_price, "shipping_fee": shipping_fee, "shipping_text": shipping_text, "total_price": crawled_price + shipping_fee, "matched": True, "reason": "수집가 기본 적용 (AI 예외)"}

    try:
        clean_json = re.sub(r"```json\s*|\s*
