import os
import re
import json
import logging
import ifcopenshell
from globals import sort_ifc_file

logger = logging.getLogger("SpatialStructureService")
logger.setLevel(logging.INFO)

def change_spatial_structure(
    input_file_path: str,
    output_file_path: str,
    site_name: str = None,
    building_name: str = None,
    storey_name: str = None,
    original_file_name: str = None
):
    """
    IFC 파일의 명칭 및 GUID 속성을 표준화합니다.
    파일명이나 입력인자를 바탕으로 Site, Building, Storey 명칭을 파싱하고
    IfcProject, IfcSite, IfcBuilding, IfcBuildingStorey의 속성을 표준값으로 업데이트합니다.
    계층 재구조화 로직 없이 기존 IFC 계층 구조를 그대로 유지합니다.
    """
    # 1. IFC 파일 로드
    model = ifcopenshell.open(input_file_path)
    
    # 2. 명칭 파싱 및 기본값 지정
    site_num = None
    building_num = None
    storey_num = None

    # 파일명에서 숫자 추출 (GUID 지정 및 이름 지정용)
    file_name = original_file_name if original_file_name else os.path.basename(input_file_path)
    match = re.search(r"M(\d{4})", file_name)
    if match:
        model_number = match.group(1)
        site_num = model_number[0:1]
        building_num = model_number[1:3]
        storey_num = model_number[3:4]

    # 예비 파싱 (매개변수로 이름이 전달되었으나 site_num, building_num이 위에서 파싱되지 않은 경우 대비)
    if not site_num and site_name:
        site_num = site_name.split("_")[0]
    if not building_num and building_name:
        building_num = building_name.split("_")[0]

    if not site_name or not building_name or not storey_name:
        if match:
            if not site_name:
                site_name = f"{site_num}_unit"
            if not building_name:
                building_name = f"{building_num}_building"
            if not storey_name:
                storey_name = f"{storey_num}_storey"
        else:
            if not site_name:
                site_name = "Default_Site"
            if not building_name:
                building_name = "Default_Building"
            if not storey_name:
                storey_name = "Default_Storey"

    logger.info(f"Target Names - Site: {site_name}, Building: {building_name}, Storey: {storey_name}")

    # 3. IfcProject 엔티티 속성 통일
    projects = model.by_type("IfcProject")
    if projects:
        project = projects[0]
        project.Name = "DKV"
        project.LongName = "Czech Dukovany 5,6"
        project.GlobalId = "230kJvmADEkxCdYYeOZMvP"

    # 4. IfcSite 엔티티 속성 통일 및 이름 업데이트
    sites = model.by_type("IfcSite")
    for site in sites:
        site.GlobalId = "1quFmiUrbACx1C6lxlbtcM"
        if site_name:
            site.Name = site_name

    # 5. IfcBuilding 엔티티 GUID 및 LongName/Name 업데이트
    building_guid = None
    building_long_name = None
    if site_num and building_num:
        guid_map_path = os.path.join(os.path.dirname(__file__), "building_guids.json")
        if os.path.exists(guid_map_path):
            try:
                with open(guid_map_path, "r", encoding="utf-8") as f:
                    guid_list = json.load(f)
                
                target_unit = int(site_num)
                target_building_code = int(building_num)
                
                for entry in guid_list:
                    if entry.get("unit") == target_unit and entry.get("building") == target_building_code:
                        building_guid = entry.get("guid")
                        building_long_name = entry.get("longName")
                        break
            except Exception as e:
                logger.error(f"Failed to load building_guids.json: {e}")

    buildings = model.by_type("IfcBuilding")
    for building in buildings:
        if building_guid:
            building.GlobalId = building_guid
        if building_long_name:
            building.LongName = building_long_name
        if building_name:
            building.Name = building_name

    # 6. IfcBuildingStorey 엔티티 이름 업데이트
    storeys = model.by_type("IfcBuildingStorey")
    for storey in storeys:
        if storey_name:
            storey.Name = storey_name

    # 7. 저장 및 Express ID 기준 정렬
    model.write(output_file_path)
    sort_ifc_file(output_file_path)
    logger.info(f"Spatial attributes successfully standardized and sorted: {output_file_path}")

# Main Space
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python spatialStructure.py <input_file.ifc>")
    else:
        input_ifc = sys.argv[1]
        output_ifc = input_ifc.replace(".ifc", "_spatial.ifc")
        change_spatial_structure(input_ifc, output_ifc)
