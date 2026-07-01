import math
import logging
import ifcopenshell
from globals import sort_ifc_file

logger = logging.getLogger("GeoreferencingService")
logger.setLevel(logging.INFO)

def inject_geographic_crs(
    file_path: str,
    output_path: str,
    eastings: float,
    northings: float,
    orthogonal_height: float = 0.0,
    rotation_angle: float = None,
    crs_name: str = "EPSG:5514",
    crs_description: str = "S-JTSK / Krovak East North",
    crs_geodetic_datum: str = "S-JTSK",
    crs_vertical_datum: str = "Baltic after adjustment",
    crs_map_projection: str = "Krovak",
    crs_map_zone: str = "Undefined",
    scale: float = 1.0,
    scale_y: float = None
):
    """
    IFC4/IFC4x3 IFC 파일에 IfcProjectedCRS와 IfcMapConversion 엔티티를 생성하거나 
    기존 엔티티를 찾아 업데이트하여 지리정보(Georeferencing)를 주입합니다.
    주입 완료 후 Express ID 기준 정렬을 수행하여 파일을 저장합니다.
    """
    # 1. IFC 파일 로드
    ifc_file = ifcopenshell.open(file_path)
    schema = ifc_file.schema
    logger.info(f"Loaded IFC model with schema: {schema}")

    # 2. 스키마 검증: IFC4 / IFC4X3 등 IFC4 계열만 지원
    # IFC2X3인 경우 예외 발생
    if schema.upper().startswith("IFC2X3"):
        raise ValueError(
            f"Unsupported schema '{schema}'. Georeferencing using native IfcProjectedCRS and IfcMapConversion is only supported in IFC4 and newer versions."
        )

    # 3. 회전각 삼각함수 계산
    # IFC 표준: XAxisAbscissa = cos(θ), XAxisOrdinate = sin(θ)
    # (CRS 좌표계(East-North)에서 로컬 X축의 동향/북향 방향 성분)
    # NEXBIM UI 라운드트립: atan2(ordinate, abscissa) = θ ✓
    xaxis_abscissa = None
    xaxis_ordinate = None
    if rotation_angle is not None:
        angle_rad = math.radians(rotation_angle)
        xaxis_abscissa = math.cos(angle_rad)
        xaxis_ordinate = math.sin(angle_rad)
        logger.info(f"Calculated 2D rotation vector: XAxisAbscissa={xaxis_abscissa:.6f}, XAxisOrdinate={xaxis_ordinate:.6f}")

    # 4. IfcGeometricRepresentationContext 찾기
    contexts = ifc_file.by_type("IfcGeometricRepresentationContext")
    context = None
    # 3D Model 컨텍스트 탐색
    for c in contexts:
        if getattr(c, "ContextType", "").upper() == "MODEL" and getattr(c, "CoordinateSpaceDimension", 0) == 3:
            context = c
            break
    # 차선책: 임의의 Model 컨텍스트 탐색
    if not context:
        for c in contexts:
            if getattr(c, "ContextType", "").upper() == "MODEL":
                context = c
                break
    # 최후 수단: 첫 번째 컨텍스트 선택
    if not context and contexts:
        context = contexts[0]

    if not context:
        raise ValueError("No IfcGeometricRepresentationContext found in the IFC file.")

    # 5. 기존 IfcMapConversion 조회
    map_conversions = ifc_file.by_type("IfcMapConversion")
    map_conversion = None
    for mc in map_conversions:
        if mc.SourceCRS == context:
            map_conversion = mc
            break

    # 6. 기존 IfcProjectedCRS 조회
    projected_crss = ifc_file.by_type("IfcProjectedCRS")
    projected_crs = None
    if map_conversion and map_conversion.TargetCRS:
        projected_crs = map_conversion.TargetCRS
    elif projected_crss:
        projected_crs = projected_crss[0]

    # 7. IfcProjectedCRS 업데이트 또는 신규 생성
    if projected_crs:
        projected_crs.Name = crs_name
        projected_crs.Description = crs_description
        projected_crs.GeodeticDatum = crs_geodetic_datum
        projected_crs.VerticalDatum = crs_vertical_datum
        projected_crs.MapProjection = crs_map_projection
        projected_crs.MapZone = crs_map_zone
        logger.info("Updated existing IfcProjectedCRS.")
    else:
        projected_crs = ifc_file.create_entity(
            "IfcProjectedCRS",
            Name=crs_name,
            Description=crs_description,
            GeodeticDatum=crs_geodetic_datum,
            VerticalDatum=crs_vertical_datum,
            MapProjection=crs_map_projection,
            MapZone=crs_map_zone
        )
        logger.info("Created new IfcProjectedCRS.")

    # 8. IfcMapConversion 업데이트 또는 신규 생성
    if map_conversion:
        map_conversion.TargetCRS = projected_crs
        map_conversion.Eastings = eastings
        map_conversion.Northings = northings
        map_conversion.OrthogonalHeight = orthogonal_height
        if xaxis_abscissa is not None:
            map_conversion.XAxisAbscissa = xaxis_abscissa
        if xaxis_ordinate is not None:
            map_conversion.XAxisOrdinate = xaxis_ordinate
        map_conversion.Scale = scale
        if scale_y is not None:
            try:
                map_conversion.ScaleY = scale_y
            except AttributeError:
                pass
        logger.info("Updated existing IfcMapConversion.")
    else:
        kwargs = {
            "SourceCRS": context,
            "TargetCRS": projected_crs,
            "Eastings": eastings,
            "Northings": northings,
            "OrthogonalHeight": orthogonal_height,
            "Scale": scale
        }
        if xaxis_abscissa is not None:
            kwargs["XAxisAbscissa"] = xaxis_abscissa
        if xaxis_ordinate is not None:
            kwargs["XAxisOrdinate"] = xaxis_ordinate

        map_conversion = ifc_file.create_entity("IfcMapConversion", **kwargs)
        if scale_y is not None:
            try:
                map_conversion.ScaleY = scale_y
            except AttributeError:
                pass
        logger.info("Created new IfcMapConversion.")

    # 9. 변경사항 저장
    ifc_file.write(output_path)
    
    # 10. Express ID 정렬
    sort_ifc_file(output_path)
    logger.info(f"Georeferencing injection completed. File saved & sorted: {output_path}")
