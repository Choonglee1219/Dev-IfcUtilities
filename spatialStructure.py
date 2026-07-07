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
    IFC 파일의 공간 구조를 재정리합니다. 
    파일명이나 입력인자를 바탕으로 Site, Building, Storey 명칭을 생성하고
    기존 계층 구조를 완전히 해제한 후 새로운 spatial hierarchy로 조립합니다.
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

    # 3. 기존 객체들 가져오기
    projects = model.by_type("IfcProject")
    if not projects:
        raise ValueError("No IfcProject found in the IFC file.")
    project = projects[0]

    # IfcProject 엔티티 속성 통일
    project.Name = "DKV"
    project.LongName = "Czech Dukovany 5,6"
    project.GlobalId = "230kJvmADEkxCdYYeOZMvP"

    buildings = model.by_type("IfcBuilding")
    if not buildings:
        raise ValueError("No IfcBuilding found in the IFC file.")

    # 기존 두 번째 건물(buildings[1])이 있으면 이를 대상 건물로 하고, 첫 번째 건물을 삭제 대상으로 지정
    if len(buildings) > 1:
        target_building = buildings[1]
        remove_building = buildings[0]
    else:
        target_building = buildings[0]
        remove_building = None

    # 외부 JSON 설정을 로드하여 site_num과 building_num 조건에 따른 IfcBuilding GUID 및 LongName 설정
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

    if building_guid:
        target_building.GlobalId = building_guid
    if building_long_name:
        target_building.LongName = building_long_name

    # OwnerHistory 조회
    owner_histories = model.by_type("IfcOwnerHistory")
    owner_history = owner_histories[0] if owner_histories else None

    # 4. 새로운 공간 구조 객체의 로컬 좌표계(ObjectPlacement) 및 엔티티 생성
    site_placement = None
    if target_building.ObjectPlacement:
        site_placement = model.create_entity(
            "IfcLocalPlacement",
            PlacementRelTo = None,
            RelativePlacement = target_building.ObjectPlacement.RelativePlacement
        )
        target_building.ObjectPlacement.PlacementRelTo = site_placement

    # 기존 IfcBuildingStorey가 있었다면 그곳의 RelativePlacement를 가져오고, 없으면 건물의 것을 기본값으로 사용
    orig_relative_placement = None
    for ost in model.by_type("IfcBuildingStorey"):
        if ost.ObjectPlacement:
            orig_relative_placement = ost.ObjectPlacement.RelativePlacement
            break
    if not orig_relative_placement and target_building.ObjectPlacement:
        orig_relative_placement = target_building.ObjectPlacement.RelativePlacement

    storey_placement = None
    if target_building.ObjectPlacement:
        storey_placement = model.create_entity(
            "IfcLocalPlacement",
            PlacementRelTo = target_building.ObjectPlacement,
            RelativePlacement = orig_relative_placement
        )

    # 모든 경우 IfcSite 엔티티의 guid는 '1quFmiUrbACx1C6lxlbtcM'로 통일
    site = model.create_entity(
        "IfcSite",
        GlobalId = "1quFmiUrbACx1C6lxlbtcM",
        OwnerHistory = owner_history,
        Name = site_name,
        Description = "Unit",
        ObjectPlacement = site_placement,
        CompositionType = "ELEMENT"
    )

    storey = model.create_entity(
        "IfcBuildingStorey",
        GlobalId = ifcopenshell.guid.new(),
        OwnerHistory = owner_history,
        Name = storey_name,
        Description = "Storey",
        ObjectPlacement = storey_placement,
        CompositionType = "ELEMENT"
    )

    # 5. 관계 분리를 위한 헬퍼 함수 정의
    def unlink_aggregation(old_el):
        rels_aggre = getattr(old_el, "Decomposes", [])
        for rel in rels_aggre:
            if getattr(rel, "RelatedObjects", None):
                relatedObjects = list(rel.RelatedObjects)
                if old_el in relatedObjects:
                    relatedObjects.remove(old_el)
                    rel.RelatedObjects = relatedObjects
                if len(rel.RelatedObjects) == 0:
                    model.remove(rel)

    def unlink_containment(old_el):
        rels_contain = getattr(old_el, "ContainedInStructure", [])
        for rel in rels_contain:
            if getattr(rel, "RelatedElements", None):
                relatedElements = list(rel.RelatedElements)
                if old_el in relatedElements:
                    relatedElements.remove(old_el)
                    rel.RelatedElements = relatedElements
                if len(rel.RelatedElements) == 0:
                    model.remove(rel)

    # 6. 모든 건물을 돌며 하위 Space와 Element(부재) 수집 및 이름 업데이트
    spaces = []
    elements = []
    
    for building in buildings:
        building.Name = building_name
        building.Description = "Building"
        unlink_aggregation(building)
        
        # 하위 공간(Spaces) 수집
        for rel in getattr(building, "IsDecomposedBy", []):
            if rel.is_a("IfcRelAggregates") and rel.RelatedObjects:
                spaces += list(rel.RelatedObjects)
                
        # 하위 부재(Elements) 수집
        for rel in getattr(building, "ContainsElements", []):
            if rel.is_a("IfcRelContainedInSpatialStructure") and rel.RelatedElements:
                elements += list(rel.RelatedElements)

    # 기존 IfcBuildingStorey에 포함되어 있는 부재도 추가로 수집
    for orig_storey in model.by_type("IfcBuildingStorey"):
        for rel in getattr(orig_storey, "ContainsElements", []):
            if rel.is_a("IfcRelContainedInSpatialStructure") and rel.RelatedElements:
                elements += list(rel.RelatedElements)

    # 중복 제거 (list -> dict.fromkeys -> list)
    spaces = list(dict.fromkeys(spaces))
    elements = list(dict.fromkeys(elements))

    # --- IfcElementAssembly (Description: PBS+ModelType) 삭제 및 하위 Design_Area 승격 로직 ---
    pbs_assemblies = [
        x for x in model.by_type("IfcElementAssembly")
        if getattr(x, "Description", None) == "PBS+ModelType"
    ]
    
    for pbs in pbs_assemblies:
        # 자식 IfcElementAssembly(Description: Design_Area) 찾기
        design_area_children = []
        for rel in getattr(pbs, "IsDecomposedBy", []):
            if rel.is_a("IfcRelAggregates") and rel.RelatedObjects:
                for child in rel.RelatedObjects:
                    if child.is_a("IfcElementAssembly") and getattr(child, "Description", None) == "Design_Area":
                        design_area_children.append(child)
                        
        # 자식 객체들의 기존 aggregation 해제 및 elements 목록에 추가
        for child in design_area_children:
            unlink_aggregation(child)
            elements.append(child)
            
        # pbs 자체의 aggregation 및 containment 해제
        unlink_aggregation(pbs)
        unlink_containment(pbs)
        
        # pbs가 RelatingObject인 IfcRelAggregates 관계 제거
        for rel in list(getattr(pbs, "IsDecomposedBy", [])):
            if rel.is_a("IfcRelAggregates"):
                model.remove(rel)
                
        # elements 목록에서 pbs 제거
        if pbs in elements:
            elements.remove(pbs)
            
        # 모델에서 pbs 엔티티 완전 삭제
        model.remove(pbs)

    # 새로 추가된 자식 객체들로 인한 중복 제거 수행
    elements = list(dict.fromkeys(elements))

    # 수집한 공간/부재들의 기존 aggregation/containment 관계 제거
    for space in spaces:
        unlink_aggregation(space)
    for element in elements:
        unlink_containment(element)

    # 7. 기존 공간 구조 삭제 및 새로운 관계 생성 (Re-structure)
    # project의 기존 IsDecomposedBy 관계들 해제 및 삭제
    for rel in list(getattr(project, "IsDecomposedBy", [])):
        if rel.is_a("IfcRelAggregates"):
            model.remove(rel)

    # 기존 모든 IfcBuildingStorey 삭제 및 관계 해제
    orig_storeys = [s for s in model.by_type("IfcBuildingStorey") if s != storey]
    for orig_storey in orig_storeys:
        unlink_aggregation(orig_storey)
        for rel in list(getattr(orig_storey, "IsDecomposedBy", [])):
            model.remove(rel)
        for rel in list(getattr(orig_storey, "ContainsElements", [])):
            model.remove(rel)
        model.remove(orig_storey)

    # 기존 모든 IfcSite 삭제 및 관계 해제
    orig_sites = [s for s in model.by_type("IfcSite") if s != site]
    for orig_site in orig_sites:
        unlink_aggregation(orig_site)
        for rel in list(getattr(orig_site, "IsDecomposedBy", [])):
            model.remove(rel)
        for rel in list(getattr(orig_site, "ContainsElements", [])):
            model.remove(rel)
        model.remove(orig_site)

    # project -> site
    model.create_entity(
        "IfcRelAggregates",
        GlobalId = ifcopenshell.guid.new(),
        OwnerHistory = owner_history,
        RelatingObject = project,
        RelatedObjects = [site]
    )

    # site -> target_building
    model.create_entity(
        "IfcRelAggregates",
        GlobalId = ifcopenshell.guid.new(),
        OwnerHistory = owner_history,
        RelatingObject = site,
        RelatedObjects = [target_building]
    )

    # target_building -> storey
    model.create_entity(
        "IfcRelAggregates",
        GlobalId = ifcopenshell.guid.new(),
        OwnerHistory = owner_history,
        RelatingObject = target_building,
        RelatedObjects = [storey]
    )

    # storey -> spaces (aggregation)
    if spaces:
        model.create_entity(
            "IfcRelAggregates",
            GlobalId = ifcopenshell.guid.new(),
            OwnerHistory = owner_history,
            RelatingObject = storey,
            RelatedObjects = spaces
        )

    # storey -> elements (containment)
    if elements:
        model.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId = ifcopenshell.guid.new(),
            OwnerHistory = owner_history,
            RelatingStructure = storey,
            RelatedElements = elements
        )

    # 8. 필요 시 첫 번째 건물 삭제
    if remove_building:
        model.remove(remove_building)

    # 9. 저장 및 정렬
    model.write(output_file_path)
    sort_ifc_file(output_file_path)
    logger.info(f"Spatial structure successfully changed and sorted: {output_file_path}")
