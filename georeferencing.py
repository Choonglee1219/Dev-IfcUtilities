import math
import logging
import ifcopenshell
import ifcopenshell.util.unit
from globals import sort_ifc_file

logger = logging.getLogger("GeoreferencingService")
logger.setLevel(logging.INFO)

def get_project_length_scale_factor(ifc_file) -> float:
    """
    IFC 모델의 IfcProject.UnitsInContext (IfcUnitAssignment)에서
    주 프로젝트 길이 단위(LENGTHUNIT)를 직접 파싱하여 SI 미터(m) 기준 Scale factor를 산출합니다.
    (예: METRE -> 1.0, MILLI METRE -> 0.001, CENTI METRE -> 0.01 등)
    """
    prefix_multipliers = {
        "EXA": 1e18, "PETA": 1e15, "TERA": 1e12, "GIGA": 1e9, "MEGA": 1e6,
        "KILO": 1e3, "HECTO": 1e2, "DECA": 1e1, "DECI": 1e-1, "CENTI": 1e-2,
        "MILLI": 1e-3, "MICRO": 1e-6, "NANO": 1e-9, "PICO": 1e-12, "FEMTO": 1e-15, "ATTO": 1e-18,
    }

    projects = ifc_file.by_type("IfcProject")
    if not projects or not projects[0].UnitsInContext:
        logger.warning("No IfcProject or UnitsInContext found. Defaulting scale to 1.0.")
        return 1.0

    unit_assignment = projects[0].UnitsInContext
    units = getattr(unit_assignment, "Units", [])

    primary_length_unit = None
    for u in units:
        if getattr(u, "UnitType", None) == "LENGTHUNIT":
            primary_length_unit = u
            break

    if not primary_length_unit:
        logger.warning("No unit with UnitType == 'LENGTHUNIT' found in UnitsInContext. Defaulting scale to 1.0.")
        return 1.0

    scale = 1.0
    target_unit = primary_length_unit
    while target_unit.is_a("IfcConversionBasedUnit"):
        conv_factor = getattr(target_unit, "ConversionFactor", None)
        if conv_factor and hasattr(conv_factor, "ValueComponent"):
            val = getattr(conv_factor.ValueComponent, "wrappedValue", conv_factor.ValueComponent)
            scale *= float(val)
            target_unit = getattr(conv_factor, "UnitComponent", None)
            if not target_unit:
                break
        else:
            break

    if target_unit and target_unit.is_a("IfcSIUnit"):
        prefix = getattr(target_unit, "Prefix", None)
        if prefix:
            scale *= prefix_multipliers.get(str(prefix).upper(), 1.0)

    logger.info(f"Primary LengthUnit: {primary_length_unit} -> Calculated Scale: {scale}")
    return scale

def deg_to_dms(deg: float):
    """
    10진수 도(Decimal Degrees) 값을 IFC 표준 (Degrees, Minutes, Seconds, Microseconds) 튜플로 변환합니다.
    """
    is_negative = deg < 0
    deg_abs = abs(deg)

    d = int(deg_abs)
    m = int((deg_abs - d) * 60)
    s = int((deg_abs - d - m / 60) * 3600)
    micro_s = int(((deg_abs - d - m / 60 - s / 3600) * 3600) * 1e6)

    if is_negative:
        d = -d

    return (d, m, s, micro_s)

def update_site_geographic_ref(ifc_file, eastings: float, northings: float, orthogonal_height: float, crs_name: str):
    """
    IfcMapConversion의 Eastings, Northings 및 crs_name을 기반으로 pyproj 좌표 변환(WGS84)을 수행하여
    IfcSite의 RefLatitude, RefLongitude, RefElevation 속성을 주입/업데이트합니다.
    """
    from pyproj import Transformer
    import ifcopenshell.guid

    lat_dms = None
    lon_dms = None

    try:
        transformer = Transformer.from_crs(crs_name, "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(eastings, northings)
        lat_dms = deg_to_dms(lat)
        lon_dms = deg_to_dms(lon)
        logger.info(f"Converted ({eastings}, {northings}) in {crs_name} to WGS84: Lat={lat} -> DMS {lat_dms}, Lon={lon} -> DMS {lon_dms}")
    except Exception as e:
        logger.warning(f"Could not calculate RefLatitude/RefLongitude from CRS '{crs_name}': {e}")

    sites = ifc_file.by_type("IfcSite")
    if not sites:
        site = ifc_file.create_entity(
            "IfcSite",
            GlobalId=ifcopenshell.guid.new(),
            Name="Default Site",
            RefElevation=orthogonal_height
        )
        if lat_dms and lon_dms:
            site.RefLatitude = lat_dms
            site.RefLongitude = lon_dms
        logger.info("Created new IfcSite and updated RefElevation, RefLatitude, RefLongitude.")
    else:
        for site in sites:
            site.RefElevation = orthogonal_height
            if lat_dms and lon_dms:
                site.RefLatitude = lat_dms
                site.RefLongitude = lon_dms
        logger.info(f"Updated {len(sites)} existing IfcSite entity/entities with RefElevation, RefLatitude, RefLongitude.")

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
    scale: float = None
):
    """
    IFC4/IFC4x3 IFC 파일에 IfcProjectedCRS와 IfcMapConversion 엔티티를 생성하거나 
    기존 엔티티를 찾아 업데이트하여 지리정보(Georeferencing)를 주입합니다.
    또한 IfcSite의 RefLatitude, RefLongitude, RefElevation 속성을 자동 조율합니다.
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

    # 3. 모델 길이 단위 분석 및 Scale 결정
    model_calculated_scale = get_project_length_scale_factor(ifc_file)
    if scale is None or scale == 1.0:
        scale = model_calculated_scale
    logger.info(f"Final scale factor applied for IfcMapConversion: {scale}")

    # 4. 회전각 삼각함수 계산
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

    # 5. IfcGeometricRepresentationContext 찾기
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

    # 6. 기존 IfcMapConversion 조회
    map_conversions = ifc_file.by_type("IfcMapConversion")
    map_conversion = None
    for mc in map_conversions:
        if mc.SourceCRS == context:
            map_conversion = mc
            break

    # 7. 기존 IfcProjectedCRS 조회
    projected_crss = ifc_file.by_type("IfcProjectedCRS")
    projected_crs = None
    if map_conversion and map_conversion.TargetCRS:
        projected_crs = map_conversion.TargetCRS
    elif projected_crss:
        projected_crs = projected_crss[0]

    # 8. IfcProjectedCRS 업데이트 또는 신규 생성
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

    # 9. IfcMapConversion 업데이트 또는 신규 생성
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
        logger.info("Created new IfcMapConversion.")

    # 10. IfcSite RefLatitude, RefLongitude, RefElevation 속성 업데이트
    update_site_geographic_ref(ifc_file, eastings, northings, orthogonal_height, crs_name)

    # 11. 변경사항 저장
    ifc_file.write(output_path)
    
    # 12. Express ID 정렬
    sort_ifc_file(output_path)
    logger.info(f"Georeferencing injection completed. File saved & sorted: {output_path}")
