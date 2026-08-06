import ifcopenshell
import requests
from globals import sort_ifc_file, round_quantities

# Definition of Adding EDB Data Function
def adding_edbData(input_file_path, 
                   output_file_path):
    model = ifcopenshell.open(input_file_path)

    # Search elements which have Tag attribute
    elements_with_edbData = []
    for element in model.by_type("IfcElement"):
        tag_no = getattr(element, "Tag", None)
        if tag_no and str(tag_no).strip():
            elements_with_edbData.append((element, str(tag_no).strip()))

    # Create list of tag_no in elements_sith_edbData
    tag_list = [{"tag_no": tag_no} for _, tag_no in elements_with_edbData]
    print(tag_list)


    # Request edbData through API
    url = "http://192.25.94.121:8080/edb/bimedbdata"
    headers = {
        "Content-Type": "application/json",
        "projAbb": "CZ",
        "systemCode": "DL"
    }
    
    data = {}
    try:
        response = requests.post(url, headers=headers, json=tag_list, timeout=15)
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as e:
        print(f"[Warning] Failed to fetch EDB data: {e}. Skipping property addition.")

    # Function : Parsing and Casting Value
    def get_cast_val(value, ifc_type_str):
        type_mapping = {
            "IFCLABLE": "IfcLabel",  # Handle API typo
            "IFCLABEL": "IfcLabel",
            "IFCBOOLEAN": "IfcBoolean",
            "IFCINTEGER": "IfcInteger",
            "IFCREAL": "IfcReal",
            "IFCTEXT": "IfcText"
        }
        ifc_type = type_mapping.get(ifc_type_str.upper() if ifc_type_str else "", "IfcLabel")
        
        try:
            if ifc_type == "IfcBoolean":
                bool_val = value.lower() in ("true", "1", "t", "yes") if isinstance(value, str) else bool(value)
                return bool_val, "IfcBoolean"
            elif ifc_type == "IfcReal":
                return float(value), "IfcReal"
            elif ifc_type == "IfcInteger":
                return int(value), "IfcInteger"
            else:
                return str(value), ifc_type
        except (ValueError, TypeError):
            return str(value), "IfcLabel"
            
    # Function : Creating Value Entity only
    def create_val_ent(model, value, ifc_type_str):
        cast_val, ifc_type = get_cast_val(value, ifc_type_str)
        return model.create_entity(ifc_type, cast_val)

    # Function : Creating IfcPropertySingleValue Entity
    def create_prop_single(model, name, value, ifc_type_str):
        return model.create_entity(
            "IfcPropertySingleValue",
            Name=name,
            NominalValue=create_val_ent(model, value, ifc_type_str),
            Unit=None
        )

    # Add PropertySet which has properties and Connect to relatedObjects
    for element, tag_no in elements_with_edbData:
        if tag_no not in data:
            continue
            
        tag_data = data[tag_no]
        if not tag_data.get("success"):
            continue
            
        # Get existing Psets for this element
        existing_psets = {}
        for rel in getattr(element, "IsDefinedBy", []):
            if rel.is_a("IfcRelDefinesByProperties"):
                pset = rel.RelatingPropertyDefinition
                if pset and pset.is_a("IfcPropertySet"):
                    existing_psets[pset.Name] = pset
                    
        for pset_data in tag_data.get("propertySets", []):
            pset_name = pset_data.get("name")
            api_props = pset_data.get("properties", [])
            
            if pset_name in existing_psets:
                # Update existing PropertySet
                existing_pset = existing_psets[pset_name]
                existing_props = {p.Name: p for p in (existing_pset.HasProperties or [])}
                
                new_props_to_add = []
                for prop in api_props:
                    prop_name = prop.get("name")
                    val = prop.get("value")
                    val_type = prop.get("ifcValueType")
                    
                    if val is None:
                        val = "TBD"
                        val_type = "IFCLABEL"
                        
                    if prop_name in existing_props:
                        # Update existing property value if changed
                        existing_prop = existing_props[prop_name]
                        existing_val = existing_prop.NominalValue.wrappedValue if getattr(existing_prop, "NominalValue", None) else None
                        expected_val, _ = get_cast_val(val, val_type)
                        
                        if existing_val != expected_val:
                            # print(f"[UPDATE] Tag: {tag_no} | Pset: {pset_name} | Property: {prop_name} | Value changed: {existing_val} -> {expected_val}")
                            existing_prop.NominalValue = create_val_ent(model, val, val_type)
                        # else:
                            # print(f"[SKIP] Tag: {tag_no} | Pset: {pset_name} | Property: {prop_name} | Value is identical: {existing_val}")
                    else:
                        # Add new property to existing Pset
                        new_props_to_add.append(create_prop_single(model, prop_name, val, val_type))
                        
                if new_props_to_add:
                    existing_pset.HasProperties = list(existing_pset.HasProperties or []) + new_props_to_add
                    
            else:
                # Create new PropertySet
                props = []
                for prop in api_props:
                    val = prop.get("value")
                    val_type = prop.get("ifcValueType")
                    
                    if val is None:
                        val = "TBD"
                        val_type = "IFCLABEL"
                        
                    props.append(create_prop_single(model, prop.get("name"), val, val_type))
                
                if props:
                    pset = model.create_entity(
                        "IfcPropertySet",
                        GlobalId=ifcopenshell.guid.new(),
                        OwnerHistory=model.by_type("IfcOwnerHistory")[0],
                        Name=pset_name,
                        Description=None,
                        HasProperties=props
                    )
                    model.create_entity(
                        "IfcRelDefinesByProperties",
                        GlobalId=ifcopenshell.guid.new(),
                        OwnerHistory=model.by_type("IfcOwnerHistory")[0],
                        Name=None,
                        Description=None,
                        RelatedObjects=[element],
                        RelatingPropertyDefinition=pset
                    )

    # 수량 데이터 소수점 셋째 자리 반올림 적용
    round_quantities(model)

    model.write(output_file_path)
    
    # 3. 작성된 IFC 파일의 DATA 섹션을 Express ID 기준으로 정렬
    sort_ifc_file(output_file_path)

    print("EDB Data are added!!")

# Main Space
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python edbData.py <input_file.ifc>")
    else:
        input_ifc = sys.argv[1]
        output_ifc = input_ifc.replace(".ifc", "_edb.ifc")
        adding_edbData(input_ifc, output_ifc)
