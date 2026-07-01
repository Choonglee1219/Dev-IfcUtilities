from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import List, Optional
import uuid
import os
import shutil
import zipfile
import json
import logging
import clash
import edbData
import editProps
import georeferencing

# --- 로깅 설정 ---
# 1. 기본 로거 레벨을 WARNING으로 설정하여 서드파티 라이브러리 로그 억제
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
# 2. 커스텀 로거는 INFO 레벨 유지
logger = logging.getLogger("ClashService")
logger.setLevel(logging.INFO)
# 3. API 엔드포인트 호출 기록 확인을 위해 uvicorn.access 로거 INFO 유지
logging.getLogger("uvicorn.access").setLevel(logging.INFO)

app = FastAPI(title="IFC Clash Detection Microservice")

# --- CORS 미들웨어 설정 추가 ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 모든 오리진 허용 (실제 운영 시에는 클라이언트 IP/도메인만 허용 권장)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic Models (input.json 구조 정의) ---

class IfcSelector(BaseModel):
    file: str
    selector: str
    mode: str

class ClashSet(BaseModel):
    name: str
    mode: str
    a: List[IfcSelector]
    b: List[IfcSelector]
    tolerance: Optional[float] = None
    clearance: Optional[float] = None
    check_all: bool

# --- Helper Functions ---

def remove_files(paths: List[str]):
    """파일 삭제 헬퍼 함수 (Background Task용)"""
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
                logger.info(f"Temporary file removed: {path}")
        except Exception as e:
            logger.error(f"Error removing file {path}: {e}")

# --- Endpoints ---

@app.post("/clash", response_class=FileResponse)
def run_clash_detection(clash_sets: List[ClashSet], background_tasks: BackgroundTasks):
    """
    input.json 형태의 데이터를 받아 간섭 체크를 수행하고 BCF 파일을 반환합니다.
    """
    # 1. 고유한 요청 ID 생성 및 임시 파일 경로 설정
    request_id = str(uuid.uuid4())
    output_filename = f"clash_result_{request_id}.bcf"
    output_path = os.path.abspath(output_filename)
    zip_filename = f"clash_result_{request_id}.zip"
    zip_path = os.path.abspath(zip_filename)

    try:
        # 2. Pydantic 모델을 dict 리스트로 변환 (clash.py 호환)
        # Pydantic v2를 사용하는 경우 model_dump(), v1인 경우 dict() 사용
        clash_data = [cs.dict() for cs in clash_sets]

        logger.info(f"Starting clash detection for request {request_id}")

        # 3. 간섭 체크 실행 (clash.py 로직)
        raw_clash_data = clash.detect_clashes(clash_data, output_path)

        # 4. BCF 후처리 (스냅샷 생성 및 XML 수정)
        bcf_file, json_file = clash.post_process_bcf(output_path, raw_clash_data)

        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="BCF file generation failed.")

        # 5. BCF와 JSON을 ZIP으로 묶기 (이중 압축 방지)
        # BCF는 이미 압축된 파일이므로 ZIP_DEFLATED 대신 ZIP_STORED를 사용하여 불필요한 재압축(CPU 소모) 시간을 대폭 제거합니다.
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zipf:
            zipf.write(bcf_file, os.path.basename(bcf_file))
            zipf.write(json_file, os.path.basename(json_file))

        # 6. 파일 응답 및 전송 후 삭제 예약
        background_tasks.add_task(remove_files, [bcf_file, json_file, zip_path])
        
        return FileResponse(
            path=zip_path,
            filename="clash_report.zip",
            media_type='application/zip'
        )

    except Exception as e:
        # 에러 발생 시 임시 파일 정리
        if os.path.exists(output_path):
            os.remove(output_path)
        json_fallback_path = os.path.splitext(output_path)[0] + "_clashes.json"
        if os.path.exists(json_fallback_path):
            os.remove(json_fallback_path)
        if os.path.exists(zip_path):
            os.remove(zip_path)
        logger.error(f"Error during clash detection: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- 신규 엔드포인트: EDB Data 추가 ---

@app.post("/add-edb-data", response_class=FileResponse)
def add_edb_data_endpoint(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """
    IFC 파일을 업로드하면 EDB API를 통해 데이터를 조회해 새로운 PropertySet을 추가하고,
    Express ID 기준으로 자동 정렬된 IFC 파일을 반환합니다.
    """
    request_id = str(uuid.uuid4())
    input_path = f"temp_input_{request_id}.ifc"
    output_path = f"temp_output_{request_id}.ifc"

    try:
        # 업로드된 파일 저장
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"Starting EDB data addition for request {request_id}")
        
        # edbData 모듈 실행
        edbData.adding_edbData(input_path, output_path)

        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="EDB Data processing failed.")

        # 파일 반환 후 임시 파일 삭제
        background_tasks.add_task(remove_files, [input_path, output_path])

        return FileResponse(
            path=output_path,
            filename=f"{file.filename.replace('.ifc', '')}_edb.ifc",
            media_type='application/octet-stream'
        )
    except Exception as e:
        remove_files([input_path, output_path])
        logger.error(f"Error in EDB Data Processing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- 신규 엔드포인트: 커스텀 Property 추가 ---

@app.post("/process-properties", response_class=FileResponse)
def process_properties_endpoint(
    background_tasks: BackgroundTasks, 
    file: UploadFile = File(...),
    action: str = Form(...),
    expressIds: str = Form(...),
    propertiesData: str = Form(...)
):
    """
    프론트엔드에서 전달받은 객체 ID(expressIds) 배열과 다중 Pset/Property(propertiesData) 정보를
    IFC 파일에 주입(add)하거나 삭제(delete)하고, Express ID 기준으로 정렬된 새 IFC 파일을 반환합니다.
    """
    request_id = str(uuid.uuid4())
    input_path = f"temp_props_input_{request_id}.ifc"
    output_path = f"temp_props_output_{request_id}.ifc"

    try:
        parsed_ids = json.loads(expressIds)
        parsed_props = json.loads(propertiesData)

        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"Starting Process Properties ({action}) for request {request_id}")
        
        # Action 분기 처리
        if action == "add":
            editProps.add_properties_to_ifc(input_path, output_path, parsed_ids, parsed_props)
        elif action == "delete":
            editProps.delete_properties_from_ifc(input_path, output_path, parsed_ids, parsed_props)
        else:
            raise ValueError(f"Unknown action: {action}")

        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="Property processing failed.")

        background_tasks.add_task(remove_files, [input_path, output_path])

        return FileResponse(
            path=output_path,
            filename=f"{file.filename.replace('.ifc', '')}_modified.ifc",
            media_type='application/octet-stream'
        )
    except Exception as e:
        remove_files([input_path, output_path])
        logger.error(f"Error in Process Properties ({action}): {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- 신규 엔드포인트: Georeferencing 주입 ---

@app.post("/inject-georeferencing", response_class=FileResponse)
def inject_georeferencing_endpoint(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    eastings: float = Form(...),
    northings: float = Form(...),
    orthogonalHeight: float = Form(0.0),
    rotationAngle: float = Form(...),
    crsName: str = Form("EPSG:5514"),
    crsDescription: str = Form("S-JTSK / Krovak East North"),
    crsGeodeticDatum: str = Form("S-JTSK"),
    crsVerticalDatum: str = Form("Baltic after adjustment"),
    crsMapProjection: str = Form("Krovak"),
    crsMapZone: str = Form("Undefined"),
    scale: float = Form(1.0),
    scaleY: Optional[float] = Form(None)
):
    """
    IFC 파일을 업로드하고 좌표 및 회전각 정보를 받아 지리정보(Georeferencing)를 주입하고,
    Express ID 기준으로 정렬된 새로운 IFC 파일을 반환합니다. (IFC4 / IFC4x3 전용)
    """
    request_id = str(uuid.uuid4())
    input_path = f"temp_geo_input_{request_id}.ifc"
    output_path = f"temp_geo_output_{request_id}.ifc"

    try:
        # 업로드된 파일 저장
        with open(input_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        logger.info(f"Starting georeferencing injection for request {request_id}")
        
        # georeferencing 모듈 호출
        georeferencing.inject_geographic_crs(
            file_path=input_path,
            output_path=output_path,
            eastings=eastings,
            northings=northings,
            orthogonal_height=orthogonalHeight,
            rotation_angle=rotationAngle,
            crs_name=crsName,
            crs_description=crsDescription,
            crs_geodetic_datum=crsGeodeticDatum,
            crs_vertical_datum=crsVerticalDatum,
            crs_map_projection=crsMapProjection,
            crs_map_zone=crsMapZone,
            scale=scale,
            scale_y=scaleY
        )

        if not os.path.exists(output_path):
            raise HTTPException(status_code=500, detail="Georeferencing injection failed.")

        # 파일 반환 후 임시 파일 삭제
        background_tasks.add_task(remove_files, [input_path, output_path])

        return FileResponse(
            path=output_path,
            filename=f"{file.filename.replace('.ifc', '')}_georeferenced.ifc",
            media_type='application/octet-stream'
        )
    except ValueError as ve:
        remove_files([input_path, output_path])
        logger.error(f"Validation error in georeferencing: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        remove_files([input_path, output_path])
        logger.error(f"Error in georeferencing: {e}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
