# IFC Utilities Microservice

이 프로젝트는 IFC 파일 간의 간섭(Clash)을 탐지하여 BCF 파일로 생성하고, 외부 EDB(Engineering Data Base) API와 연동하여 IFC 모델에 프로퍼티 데이터를 주입하는 통합 마이크로서비스입니다. FastAPI를 기반으로 구축되었습니다.

## 주요 기능

*   **간섭 탐지 (Clash Detection)**: 사용자가 정의한 규칙(Clash Set)에 따라 IFC 모델 간의 물리적 간섭을 분석합니다.
*   **BCF 리포트 생성**: 분석 결과를 표준 BCF 2.1 형식(.bcf)으로 내보냅니다.
*   **지능형 후처리 (Post-processing)**:
    *   생성된 BCF 패키지 내의 XML을 수정하여 뷰어 호환성을 개선합니다.
    *   간섭이 발생한 두 객체에 대해 시각적 구분을 위한 색상(빨강/초록) 정보를 주입합니다.
    *   기본 스냅샷(Snapshot) 이미지를 생성하여 포함시킵니다.
*   **EDB 데이터 연동 및 IFC 속성 추가**: IFC 파일을 업로드하면 EDB API를 조회하여 태그(`KENC_Tag`)가 일치하는 요소에 새로운 PropertySet을 자동으로 추가하거나 업데이트합니다.
*   **커스텀 프로퍼티 조작 (추가/삭제)**: 객체의 Express ID 배열과 수정할 PropertySet 데이터를 기반으로, 기존의 일대다 공유 관계(Pset Sharing)를 해치지 않으면서 정교하게 속성을 주입(`add`)하거나 삭제(`delete`)하고 고아 엔티티를 정리합니다.
*   **IFC 파일 자동 정렬**: 데이터가 추가된 IFC 파일의 DATA 섹션을 ExpressID 기준으로 오름차순 정렬합니다.
*   **REST API 제공**: HTTP POST 요청을 통해 간섭 체크를 요청하거나 데이터가 병합/수정된 파일 결과를 다운로드할 수 있습니다.

## 설치 방법

1. 저장소를 복제합니다.
   ```bash
   git clone https://github.com/your_id/Dev-IfcUtilities.git
   cd Dev-IfcUtilities
   ```

2. Python 가상 환경을 생성하고 활성화합니다.
   ```bash
   python -m venv .venv
   
   # Windows
   .venv\Scripts\activate
   
   # macOS/Linux
   source .venv/bin/activate
   ```

3. 필요한 의존성 패키지를 설치합니다.
   ```bash
   pip install -r requirements.txt
   ```

## 실행 방법

### 1. API 서버 실행 (권장)

FastAPI 서버를 실행하여 외부 요청을 처리할 수 있습니다.

```bash
python main.py
# 또는 uvicorn 직접 실행
uvicorn main:app --host 0.0.0.0 --port 8000
```

서버가 실행되면 `http://localhost:8000/docs` 에서 Swagger UI를 통해 API를 테스트할 수 있습니다.

### 2. 독립 실행 (Standalone)

API 서버 없이 로컬에서 테스트하려면 개별 스크립트를 직접 실행할 수 있습니다.

```bash
# 1. 간섭 체크 모듈 테스트 (clash.py 내부 경로 수정 필요)
python clash.py

# 2. EDB 데이터 추가 모듈 테스트
python edbData.py <input_file.ifc>
```

## API 사용법

### 1. 간섭 체크 (Clash Detection)

*   **Endpoint**: `POST /clash`
*   **Content-Type**: `application/json`
*   **Response**: `clash_report.zip` 파일 다운로드 (BCF 및 JSON 포함)

**요청 본문 (JSON) 예시:**

```json
[
  {
    "name": "Structural vs MEP",
    "mode": "intersection",
    "tolerance": 0.01,
    "check_all": true,
    "a": [
      { "file": "C:/path/to/structure.ifc", "selector": "IfcBeam", "mode": "include" }
    ],
    "b": [
      { "file": "C:/path/to/mep.ifc", "selector": "IfcDuctSegment", "mode": "include" }
    ]
  }
]
```

### 2. EDB 데이터 연동 및 프로퍼티 추가 (Add EDB Data)
*   **Endpoint**: `POST /add-edb-data` 
*   **Content-Type**: `multipart/form-data`
*   **Request Body**: `file` (업로드할 원본 IFC 파일 첨부) 
*   **Response**: `[원본파일명]_edb.ifc` 파일 다운로드 (프로퍼티가 업데이트되고 ExpressID순으로 정렬된 IFC 파일)

### 3. 커스텀 프로퍼티 조작 (Process Custom Properties)
*   **Endpoint**: `POST /process-properties`
*   **Content-Type**: `multipart/form-data`
*   **Request Body**:
    *   `file`: 업로드할 원본 IFC 파일 (`.ifc` 형식)
    *   `action`: 수행할 작업 (`add` 또는 `delete`)
    *   `expressIds`: 속성을 변경할 대상 객체의 Express ID 배열 (JSON String 형식, 예: `"[123, 456]"`)
    *   `propertiesData`: 추가/삭제할 PropertySet 및 Property 정보 (JSON String 형식)
        *   예: `"[{\"name\": \"Pset_Custom\", \"props\": [{\"name\": \"PropName\", \"value\": \"PropValue\"}]}]"`
*   **Response**: `[원본파일명]_modified.ifc` 파일 다운로드 (프로퍼티가 추가/삭제되고 ExpressID순으로 정렬된 IFC 파일)
