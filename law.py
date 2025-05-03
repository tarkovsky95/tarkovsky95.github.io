# law.py (수정본 - 게시 날짜 확인 기능 추가 및 Gemini 2.5 Pro 사용)

import os
import time
import glob
import re
import traceback # 오류 추적용

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from datetime import datetime, timezone, timedelta

import PyPDF2 # PDF 처리를 위한 라이브러리
import google.generativeai as genai # Gemini API 라이브러리

# --- 설정 ---
# 대상 URL
URL = "https://www.nars.go.kr/report/list.do?cmsCode=CM0043"
# 다운로드 폴더 설정 (GitHub Actions 환경 고려)
DOWNLOAD_DIR = os.path.join(os.getcwd(), "downloads")
# 저장될 게시물 폴더 (Handmade Blog 템플릿 기준)
POSTS_DIR = "_articles"
# 다운로드 대기 최대 시간 (초)
DOWNLOAD_WAIT_TIMEOUT = 120 # 2분
# Gemini API 키 (환경 변수에서 읽기)
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# --- 함수 정의 ---

def setup_driver(download_dir):
    """Headless Chrome 드라이버를 설정하고 다운로드 폴더를 지정합니다."""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920x1080")
    prefs = {
        "download.default_directory": download_dir,
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "plugins.always_open_pdf_externally": True  # PDF 뷰어 대신 바로 다운로드
    }
    chrome_options.add_experimental_option("prefs", prefs)
    try:
        # WebDriver Manager를 사용하여 ChromeDriver 자동 설치 및 경로 설정
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print(f"Chrome 드라이버 (Headless) 설정 완료. 다운로드 폴더: {download_dir}")
        return driver
    except ValueError as e:
        # WebDriver Manager가 드라이버를 찾지 못하는 경우 등 처리
        print(f"WebDriver 설정 중 오류 발생: {e}")
        print("시스템에 Chrome이 설치되어 있는지 확인하거나, GitHub Actions 환경의 Runner 구성을 확인하세요.")
        raise

def wait_for_download_complete(download_dir, timeout):
    """지정된 폴더에 PDF 파일 다운로드가 완료될 때까지 대기합니다."""
    print(f"'{download_dir}' 폴더에서 PDF 다운로드 완료를 대기합니다 (최대 {timeout}초)...")
    start_time = time.time()
    downloaded_pdf_path = None

    while time.time() - start_time < timeout:
        # 다운로드 중인 임시 파일(.crdownload) 확인
        crdownload_files = glob.glob(os.path.join(download_dir, "*.crdownload"))
        if crdownload_files:
            print(f"다운로드 중 파일 감지: {crdownload_files}, 1초 대기...")
            time.sleep(1)
            continue # 아직 다운로드 중이므로 다시 확인

        # PDF 파일 확인
        pdf_files = glob.glob(os.path.join(download_dir, "*.pdf"))
        if pdf_files:
            # 가장 최근에 수정된 파일 선택 (새로 다운로드된 파일일 가능성 높음)
            latest_file = max(pdf_files, key=os.path.getmtime)
            try:
                # 파일 크기 변화를 통해 다운로드 완료 여부 판단
                current_size = os.path.getsize(latest_file)
                print(f"PDF 파일 감지: {os.path.basename(latest_file)}, 크기: {current_size} bytes. 1초 후 크기 변화 확인...")
                time.sleep(1) # 파일 쓰기 완료 시간 확보
                final_size = os.path.getsize(latest_file)
                if current_size == final_size and current_size > 0: # 크기 변화 없고, 0 바이트가 아니면 완료로 간주
                    print(f"다운로드 완료 확인: {os.path.basename(latest_file)}")
                    downloaded_pdf_path = latest_file
                    break # 다운로드 완료, 반복 중단
                else:
                    print(f"파일 크기 변경 감지 또는 크기 0. 다운로드 진행 중...")
            except FileNotFoundError:
                # 파일이 갑자기 사라진 경우 (예: 사용자가 수동 삭제)
                print(f"파일 접근 중 오류 발생 (파일이 삭제되었을 수 있음): {latest_file}, 계속 대기...")
                time.sleep(1)
            except Exception as e:
                print(f"파일 크기 확인 중 예상치 못한 오류: {e}")
                time.sleep(1)
        else:
            # 아직 PDF 파일이 없음
            print("다운로드된 PDF 파일 없음, 1초 대기...")
            time.sleep(1)

    if downloaded_pdf_path:
        return downloaded_pdf_path
    else:
        # 지정된 시간 내에 다운로드가 완료되지 않음
        raise TimeoutException(f"다운로드 시간 초과({timeout}초) 또는 PDF 파일을 찾을 수 없습니다.")

def extract_text_from_pdf(pdf_path):
    """PDF 파일에서 텍스트를 추출합니다."""
    print(f"PDF 파일에서 텍스트 추출 중: {pdf_path}")
    text = ""
    try:
        with open(pdf_path, 'rb') as file: # 바이너리 읽기 모드
            reader = PyPDF2.PdfReader(file)
            num_pages = len(reader.pages)
            print(f"총 {num_pages} 페이지")
            for page_num in range(num_pages):
                try:
                    page = reader.pages[page_num]
                    extracted_page_text = page.extract_text()
                    if extracted_page_text: # None이 아닌 경우에만 추가
                        text += extracted_page_text
                except Exception as page_e:
                    # 특정 페이지 추출 실패 시 오류 메시지 출력 후 계속 진행
                    print(f"{page_num + 1}번째 페이지 텍스트 추출 중 오류: {page_e}")
            print(f"텍스트 추출 완료 (총 {len(text)}자)")
            return text
    except Exception as e:
        print(f"PDF 텍스트 추출 중 오류 발생: {e}")
        raise # 상위 호출자로 예외 전파

def generate_blog_post_with_gemini(api_key, pdf_text):
    """Gemini API를 사용하여 주어진 텍스트로 블로그 글을 생성합니다."""
    if not api_key:
        raise ValueError("GEMINI_API_KEY 환경 변수가 설정되지 않았습니다.")
    if not pdf_text or pdf_text.strip() == "":
        # PDF에서 추출된 텍스트가 비어있는 경우
        raise ValueError("PDF에서 추출된 텍스트가 비어 있습니다. 블로그 글을 생성할 수 없습니다.")
    print("Gemini API 설정 및 호출 시작...")
    try:
        genai.configure(api_key=api_key)
        # Gemini 모델을 'gemini-2.5-pro-exp-03-25'로 변경
        model = genai.GenerativeModel('gemini-2.5-pro-exp-03-25')
        print(f"Gemini 모델 '{model.model_name}' 사용 중...")

        prompt = f"""
        당신은 국회입법조사처의 보고서 내용을 일반 대중이 이해하기 쉽게 **Markdown 형식의 블로그 게시물**로 재작성하는 AI 어시스턴트입니다. 최종 목표는 GitHub Pages 블로그('Handmade Blog' 템플릿 사용)에 게시할 수 있는 `.md` 파일을 만드는 것입니다.

        **작성 가이드라인: POSST 구조 기반**
        결과물의 내용 흐름은 아래 설명된 **POSST 이론**의 구조를 따라야 합니다. 하지만 최종 결과물에는 **`[펀치라인]`, `[개요]` 와 같은 명시적인 레이블을 절대 포함하지 마세요.** 각 단계의 목적이 글의 흐름 속에서 자연스럽게 드러나도록 작성해야 합니다.

        **POSST 이론 설명 (내부 참고용):**
        * **Punchline (펀치라인):** 보고서의 가장 핵심적인 결론이나 독자의 시선을 사로잡는 내용을 강력한 첫 문단 또는 첫 몇 문장으로 제시.
        * **Overview (개요):** 보고서의 주제와 중요성을 간략히 소개하며 글의 전체적인 방향 제시.
        * **Storyline (스토리라인):** 보고서의 주요 내용, 근거, 분석 등을 논리적으로 설명. 필요시 Markdown 소제목(## 또는 ###)으로 내용을 구분하여 가독성 높임.
        * **Summary (요약):** 보고서의 핵심 내용을 간결하게 요약하며 메시지 재강조.
        * **Touch Point (터치포인트/공감 포인트):** 보고서 내용과 독자를 연결하고, 생각할 거리나 행동 제안 등으로 마무리.

        **최종 결과물 요구사항 (Markdown 형식):**
        1.  **전체 형식:** 반드시 **Markdown 문법**을 사용하여 작성해주세요.
        2.  **제목:** 글의 맨 처음에 Markdown의 가장 큰 제목 형식 (`# 제목`)을 사용하여 전체 블로그 게시물의 제목을 작성해주세요. (Front Matter의 title과 별개로 본문에도 포함)
        3.  **소제목 (Headings):** 내용의 구조를 명확히 하고 가독성을 높이기 위해, **Storyline** 부분 등 필요한 곳에 Markdown 소제목 (`## 소제목` 또는 `### 하위 소제목`)을 적절히 사용해주세요.
        4.  **본문:** 단락 구분 명확히(빈 줄 사용). 필요시 **굵은 글씨**, *기울임꼴*, 목록 사용.
        5.  **가독성 및 톤:** **일반 대중**이 이해하기 쉽도록 쉬운 용어, 간결한 문장. 보고서 내용에 **충실**하되 **자연스러운 블로그 글** 어조 유지. POSST 구조 따르되 명시적 레이블 금지.
        6.  **사례:** 보고서에서 명확히 가상이라고 언급하지 않는 한 사실로 간주. 불확실하면 가상이라고 단정하지 말 것.

        --- 보고서 내용 시작 ---
        {pdf_text}
        --- 보고서 내용 끝 ---

        **이제 위의 모든 가이드라인과 보고서 내용을 바탕으로, 완결된 Markdown 형식의 블로그 게시물 본문 전체를 작성해주세요.**
        """
        # Gemini 모델의 입력 토큰 제한 고려 (필요시 pdf_text 길이 조절)
        max_input_length = 1000000 # gemini-1.5-pro-latest는 컨텍스트 창이 매우 큼
        if len(pdf_text) > max_input_length:
            print(f"입력 텍스트가 너무 깁니다 ({len(pdf_text)}자). 앞부분 {max_input_length}자만 사용합니다.")
            pdf_text_input = pdf_text[:max_input_length]
        else:
            pdf_text_input = pdf_text

        response = model.generate_content(prompt)
        print("Gemini API 응답 수신 완료.")

        # 응답 객체에서 텍스트 추출 및 유효성 검사
        if hasattr(response, 'text'):
            blog_post = response.text
            # 응답 시작/끝에 불필요한 마크다운 코드 블록 마커 제거
            blog_post = re.sub(r'^```markdown\s*', '', blog_post, flags=re.IGNORECASE)
            blog_post = re.sub(r'\s*```$', '', blog_post)
            return blog_post.strip()
        elif hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
            # API에서 콘텐츠 생성을 차단한 경우
            reason = response.prompt_feedback.block_reason
            print(f"콘텐츠 생성 차단됨. 이유: {reason}")
            raise ValueError(f"Gemini 콘텐츠 생성 차단됨: {reason}")
        else:
            # 예상치 못한 응답 형식
            print("오류: Gemini API 응답에서 유효한 텍스트를 추출할 수 없습니다.")
            # print("전체 응답:", response) # 디버깅 시 전체 응답 내용 확인
            raise ValueError("Gemini API 응답 형식 오류")

    except Exception as e:
        print(f"Gemini API 호출 중 오류 발생: {e}")
        raise

def save_markdown_post(markdown_content):
    """생성된 마크다운 내용을 Front Matter와 함께 파일로 저장합니다."""
    try:
        print("생성된 내용을 블로그 포스트 파일로 저장 시도...")

        cleaned_output = markdown_content.strip()
        if not cleaned_output:
            raise ValueError("저장할 마크다운 내용이 비어 있습니다.")

        # 마크다운 본문 첫 줄에서 제목 추출 (Front Matter용)
        title_line = cleaned_output.split('\n', 1)[0]
        # 제목에서 Markdown '#' 제거 및 YAML 따옴표 이스케이프 (큰따옴표가 제목에 있을 경우 대비)
        post_title_for_frontmatter = title_line.lstrip('# ').strip().replace('"', '\\"')
        if not post_title_for_frontmatter:
            post_title_for_frontmatter = "무제 보고서" # 제목이 비어있을 경우 기본값
        print(f"Frontmatter용 제목: {post_title_for_frontmatter}")

        # 파일명용 슬러그 생성 (한글 허용 및 개선)
        slug_base_title = title_line.lstrip('# ').strip() # 슬러그 생성용 원본 제목
        slug = re.sub(r'[^\w\s\-가-힣]+', '', slug_base_title.lower()) # 영문/숫자/공백/하이픈/한글 외 제거
        slug = re.sub(r'\s+', '-', slug).strip('-') # 공백을 하이픈으로 변경
        slug = re.sub(r'--+', '-', slug) # 연속된 하이픈을 하나로 변경
        if not slug: slug = "report" # 슬러그가 비어있을 경우 기본값 사용
        print(f"생성된 슬러그: {slug}")

        # KST (한국 표준시) 기준 현재 시간
        kst = timezone(timedelta(hours=9))
        now = datetime.now(kst)

        # --- ID 
