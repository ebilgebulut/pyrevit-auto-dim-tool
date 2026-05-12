# -*- coding: utf-8 -*-
"""
pyRevit Auto Dim Tool

Creates grid and facade dimensions across selected Revit plan views.

Author: Elif Bilge Bulut
Project: BIM Automation Portfolio
Revit: 2025 / 2026
Status: Early public release
"""

import clr
clr.AddReference("RevitAPI")
clr.AddReference("RevitAPIUI")
clr.AddReference("PresentationFramework")

from Autodesk.Revit.DB import *
from pyrevit import forms
from System.Windows import Thickness
from System.Windows.Controls import CheckBox, ComboBoxItem

uidoc = __revit__.ActiveUIDocument
if uidoc is None:
    forms.alert("No active Revit document found.")
    raise Exception("ActiveUIDocument not found.")

doc = uidoc.Document


def element_id_int(element_or_id):
    """
    Revit 2025/2026 compatibility helper for ElementId numeric values.
    Revit 2026 may expose ElementId.Value instead of IntegerValue.
    Accepts either an Element or an ElementId.
    """
    try:
        eid = element_or_id.Id
    except:
        eid = element_or_id

    try:
        return int(eid.Value)
    except:
        pass

    try:
        return int(eid.IntegerValue)
    except:
        pass

    return int(eid)

NORTH_RAY = XYZ(0, -1, 0)
SOUTH_RAY = XYZ(0, 1, 0)
EAST_RAY  = XYZ(-1, 0, 0)
WEST_RAY  = XYZ(1, 0, 0)
SAMPLE_COUNT = 80
RAY_OFFSET_MM = 1500


def mm_to_internal(mm):
    return UnitUtils.ConvertToInternalUnits(mm, UnitTypeId.Millimeters)


def get_plan_view_z(plan_view):
    """
    Returns the correct Z/elevation for drawing dimension lines in a specific plan view.
    This keeps dimensions visible on upper floor plans instead of creating them at Z=0.
    """
    try:
        if hasattr(plan_view, "GenLevel") and plan_view.GenLevel is not None:
            return plan_view.GenLevel.Elevation
    except:
        pass

    try:
        if plan_view.SketchPlane is not None:
            return plan_view.SketchPlane.GetPlane().Origin.Z
    except:
        pass

    try:
        bbox = plan_view.CropBox
        if bbox is not None:
            return (bbox.Min.Z + bbox.Max.Z) / 2.0
    except:
        pass

    return 0.0


def get_element_name(elem):
    try:
        return Element.Name.GetValue(elem)
    except:
        pass
    try:
        p = elem.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if p and p.HasValue:
            return p.AsString()
    except:
        pass
    try:
        p = elem.get_Parameter(BuiltInParameter.ALL_MODEL_TYPE_NAME)
        if p and p.HasValue:
            return p.AsString()
    except:
        pass
    try:
        return elem.FamilyName
    except:
        pass
    return "<Unnamed>"


def get_all_candidate_plan_views(document):
    views = FilteredElementCollector(document).OfClass(ViewPlan).WhereElementIsNotElementType().ToElements()
    result = []
    for v in views:
        if not v.IsTemplate:
            result.append(v)
    return sorted(result, key=lambda x: get_element_name(x))


def get_linear_dimension_types(document):
    dim_types = FilteredElementCollector(document).OfClass(DimensionType).WhereElementIsElementType().ToElements()
    result = []
    for dt in dim_types:
        try:
            if dt.StyleType == DimensionStyleType.Linear:
                result.append(dt)
        except:
            pass
    return sorted(result, key=lambda x: get_element_name(x))


def get_first_working_3d_view(document):
    for v in FilteredElementCollector(document).OfClass(View3D):
        if not v.IsTemplate:
            return v
    return None


def create_dimension_with_type(plan_view, line, ref_array, dim_type):
    if not ref_array or ref_array.Size <= 1:
        return False
    try:
        if dim_type is not None:
            doc.Create.NewDimension(plan_view, line, ref_array, dim_type)
        else:
            doc.Create.NewDimension(plan_view, line, ref_array)
        return True
    except Exception as ex:
        print("Dimension failed in {0}: {1}".format(get_element_name(plan_view), str(ex)))
        return False


# --------------------------------------------------
# TRUE FACADE BOUNDS / OFFSET HELPERS
# --------------------------------------------------
def make_bounds_from_cropbox(bbox):
    return {
        "west": bbox.Min.X,
        "east": bbox.Max.X,
        "south": bbox.Min.Y,
        "north": bbox.Max.Y
    }


def get_exterior_bounds_from_wall_items(horizontal_items, vertical_items, bbox):
    bounds = make_bounds_from_cropbox(bbox)

    east_values = []
    west_values = []
    north_values = []
    south_values = []

    for item in vertical_items:
        wall = item["wall"]
        face, face_ref = get_exterior_face_ref_for_side(wall, EAST_RAY)
        if face:
            east_values.append(face.Origin.X)
        face, face_ref = get_exterior_face_ref_for_side(wall, WEST_RAY)
        if face:
            west_values.append(face.Origin.X)

    for item in horizontal_items:
        wall = item["wall"]
        face, face_ref = get_exterior_face_ref_for_side(wall, NORTH_RAY)
        if face:
            north_values.append(face.Origin.Y)
        face, face_ref = get_exterior_face_ref_for_side(wall, SOUTH_RAY)
        if face:
            south_values.append(face.Origin.Y)

    if east_values:
        bounds["east"] = max(east_values)
    if west_values:
        bounds["west"] = min(west_values)
    if north_values:
        bounds["north"] = max(north_values)
    if south_values:
        bounds["south"] = min(south_values)

    return bounds

# --------------------------------------------------
# GRID DIMENSION ENGINE
# --------------------------------------------------
def create_grid_refs(grids_list):
    ref_array = ReferenceArray()
    for g in grids_list:
        ref_array.Append(Reference(g))
    return ref_array


def create_grid_overall_refs(grids_list):
    ref_array = ReferenceArray()
    ref_array.Append(Reference(grids_list[0]))
    ref_array.Append(Reference(grids_list[-1]))
    return ref_array


def get_grid_center(g):
    return g.Curve.Evaluate(0.5, True)


def process_grid_view(plan_view, grid_segment_offset_mm, grid_overall_offset_mm, create_segment, create_overall, dim_type, view3d=None):
    bbox = plan_view.CropBox
    if bbox is None:
        return False, "grid: no crop box"

    bounds = make_bounds_from_cropbox(bbox)
    if view3d is not None:
        try:
            horizontal_items, vertical_items, exterior_wall_ids, facade_bounds = collect_facade_wall_data(doc, plan_view, view3d)
            bounds = facade_bounds
        except:
            bounds = make_bounds_from_cropbox(bbox)

    grids = FilteredElementCollector(doc, plan_view.Id).OfClass(Grid).WhereElementIsNotElementType().ToElements()
    if len(grids) < 2:
        return False, "grid: not enough grids"

    x_grids = []
    y_grids = []
    for g in grids:
        curve = g.Curve
        if not isinstance(curve, Line):
            continue
        direction = curve.Direction
        if abs(direction.X) > abs(direction.Y):
            x_grids.append(g)
        else:
            y_grids.append(g)

    x_grids = sorted(x_grids, key=lambda g: get_grid_center(g).Y)
    y_grids = sorted(y_grids, key=lambda g: get_grid_center(g).X)

    if len(x_grids) < 2 and len(y_grids) < 2:
        return False, "grid: not enough linear grids"

    segment_offset = mm_to_internal(grid_segment_offset_mm)
    overall_offset = mm_to_internal(grid_overall_offset_mm)
    view_z = get_plan_view_z(plan_view)

    def line_x(side, offset_val):
        x = bounds["west"] - offset_val if side == "left" else bounds["east"] + offset_val
        return Line.CreateBound(XYZ(x, bounds["south"], view_z), XYZ(x, bounds["north"], view_z))

    def line_y(side, offset_val):
        y = bounds["south"] - offset_val if side == "bottom" else bounds["north"] + offset_val
        return Line.CreateBound(XYZ(bounds["west"], y, view_z), XYZ(bounds["east"], y, view_z))

    created = 0

    if len(x_grids) > 1:
        if create_segment:
            refs = create_grid_refs(x_grids)
            if create_dimension_with_type(plan_view, line_x("left", segment_offset), refs, dim_type): created += 1
            if create_dimension_with_type(plan_view, line_x("right", segment_offset), refs, dim_type): created += 1
        if create_overall:
            refs = create_grid_overall_refs(x_grids)
            if create_dimension_with_type(plan_view, line_x("left", overall_offset), refs, dim_type): created += 1
            if create_dimension_with_type(plan_view, line_x("right", overall_offset), refs, dim_type): created += 1

    if len(y_grids) > 1:
        if create_segment:
            refs = create_grid_refs(y_grids)
            if create_dimension_with_type(plan_view, line_y("bottom", segment_offset), refs, dim_type): created += 1
            if create_dimension_with_type(plan_view, line_y("top", segment_offset), refs, dim_type): created += 1
        if create_overall:
            refs = create_grid_overall_refs(y_grids)
            if create_dimension_with_type(plan_view, line_y("bottom", overall_offset), refs, dim_type): created += 1
            if create_dimension_with_type(plan_view, line_y("top", overall_offset), refs, dim_type): created += 1

    if created > 0:
        return True, "grid: {0} dimensions created".format(created)
    return False, "grid: no dimensions created"


# --------------------------------------------------
# FACADE DIMENSION ENGINE
# Based on the PythonShell facade script, refactored for selected views + UI options.
# --------------------------------------------------
def get_any_exterior_face_ref(wall):
    try:
        refs = HostObjectUtils.GetSideFaces(wall, ShellLayerType.Exterior)
    except:
        return None, None
    for r in refs:
        try:
            face = wall.GetGeometryObjectFromReference(r)
        except:
            face = None
        if isinstance(face, PlanarFace):
            return face, r
    return None, None


def get_exterior_face_ref_for_side(wall, ray_dir):
    try:
        refs = HostObjectUtils.GetSideFaces(wall, ShellLayerType.Exterior)
    except:
        return None, None
    for r in refs:
        try:
            face = wall.GetGeometryObjectFromReference(r)
        except:
            face = None
        if not isinstance(face, PlanarFace):
            continue
        n = face.FaceNormal
        if ray_dir.X == 0 and ray_dir.Y == -1:
            if n.Y > 0.95: return face, r
        elif ray_dir.X == 0 and ray_dir.Y == 1:
            if n.Y < -0.95: return face, r
        elif ray_dir.X == -1 and ray_dir.Y == 0:
            if n.X > 0.95: return face, r
        elif ray_dir.X == 1 and ray_dir.Y == 0:
            if n.X < -0.95: return face, r
    return None, None


def get_wall_orientation_group(wall):
    loc = wall.Location
    if not isinstance(loc, LocationCurve): return "other"
    curve = loc.Curve
    if not isinstance(curve, Line): return "other"
    direction = curve.Direction
    if abs(direction.X) > abs(direction.Y): return "horizontal"
    if abs(direction.Y) > abs(direction.X): return "vertical"
    return "other"


def collect_walls_from_side(document, view3d, start_line, ray_dir):
    intersector = ReferenceIntersector(ElementClassFilter(Wall), FindReferenceTarget.Element, view3d)
    hit_walls = {}
    for i in range(SAMPLE_COUNT + 1):
        t = float(i) / SAMPLE_COUNT
        sample_pt = start_line.Evaluate(t, True)
        result = intersector.FindNearest(sample_pt, ray_dir)
        if not result: continue
        ref = result.GetReference()
        elem = document.GetElement(ref)
        if isinstance(elem, Wall):
            hit_walls[element_id_int(elem.Id)] = elem
    return list(hit_walls.values())


def unique_walls(walls):
    result = {}
    for w in walls:
        result[element_id_int(w.Id)] = w
    return list(result.values())


def append_ref_item(document, ref_items, owner, ref, coord, label):
    if ref is None or coord is None: return
    try:
        stable = ref.ConvertToStableRepresentation(document)
    except:
        owner_id = element_id_int(owner.Id) if owner else -1
        stable = label + "_" + str(coord) + "_" + str(owner_id)
    ref_items.append({"owner": owner, "ref": ref, "coord": coord, "label": label, "stable": stable})


def build_reference_array_from_items(ref_items):
    ref_array = ReferenceArray()
    used = set()
    for item in sorted(ref_items, key=lambda x: x["coord"]):
        if item["stable"] in used: continue
        ref_array.Append(item["ref"])
        used.add(item["stable"])
    if ref_array.Size > 1: return ref_array
    return None


def collect_insert_candidates_from_wall(wall):
    ids = wall.FindInserts(True, False, False, False)
    elems = []
    for eid in ids:
        e = doc.GetElement(eid)
        if e: elems.append(e)
    return elems


def get_bbox_minmax_x(elem, view):
    try: bb = elem.get_BoundingBox(view)
    except: bb = None
    if bb is None: return None, None
    return bb.Min.X, bb.Max.X


def get_bbox_minmax_y(elem, view):
    try: bb = elem.get_BoundingBox(view)
    except: bb = None
    if bb is None: return None, None
    return bb.Min.Y, bb.Max.Y


def add_opening_refs_for_facade(document, plan_view, facade_name, middle_items, ref_items):
    for item in middle_items:
        wall = item["wall"]
        inserts = collect_insert_candidates_from_wall(wall)
        for ins in inserts:
            try: left_refs = ins.GetReferences(FamilyInstanceReferenceType.Left)
            except: left_refs = []
            try: right_refs = ins.GetReferences(FamilyInstanceReferenceType.Right)
            except: right_refs = []
            if facade_name in ["EAST", "WEST"]:
                min_v, max_v = get_bbox_minmax_y(ins, plan_view)
                if min_v is None or max_v is None: continue
                if len(left_refs) > 0: append_ref_item(document, ref_items, ins, left_refs[0], min_v, "insert_left_ref_bbox_minY")
                if len(right_refs) > 0: append_ref_item(document, ref_items, ins, right_refs[0], max_v, "insert_right_ref_bbox_maxY")
            else:
                min_v, max_v = get_bbox_minmax_x(ins, plan_view)
                if min_v is None or max_v is None: continue
                if len(left_refs) > 0: append_ref_item(document, ref_items, ins, left_refs[0], min_v, "insert_left_ref_bbox_minX")
                if len(right_refs) > 0: append_ref_item(document, ref_items, ins, right_refs[0], max_v, "insert_right_ref_bbox_maxX")


def get_joined_horizontal_walls_from_vertical_wall(vertical_wall):
    result = {}
    loc = vertical_wall.Location
    if not isinstance(loc, LocationCurve): return []
    for end in [0, 1]:
        try: joined = loc.ElementsAtJoin[end]
        except: joined = None
        if joined is None: continue
        for elem in joined:
            if element_id_int(elem.Id) == element_id_int(vertical_wall.Id): continue
            if not isinstance(elem, Wall): continue
            if get_wall_orientation_group(elem) != "horizontal": continue
            result[element_id_int(elem.Id)] = elem
    return list(result.values())


def get_joined_vertical_walls_from_horizontal_wall(horizontal_wall):
    result = {}
    loc = horizontal_wall.Location
    if not isinstance(loc, LocationCurve): return []
    for end in [0, 1]:
        try: joined = loc.ElementsAtJoin[end]
        except: joined = None
        if joined is None: continue
        for elem in joined:
            if element_id_int(elem.Id) == element_id_int(horizontal_wall.Id): continue
            if not isinstance(elem, Wall): continue
            if get_wall_orientation_group(elem) != "vertical": continue
            result[element_id_int(elem.Id)] = elem
    return list(result.values())


def get_wall_length(wall):
    try:
        loc = wall.Location
        if isinstance(loc, LocationCurve):
            return loc.Curve.Length
    except:
        pass
    return 0.0


def get_joined_walls_any_orientation(wall):
    """
    One-step joined-wall expansion helper.
    Finds walls joined to both ends of a perimeter wall.
    """
    result = {}

    try:
        loc = wall.Location
    except:
        loc = None

    if not isinstance(loc, LocationCurve):
        return []

    for end in [0, 1]:
        try:
            joined = loc.ElementsAtJoin[end]
        except:
            joined = None

        if joined is None:
            continue

        for elem in joined:
            if elem is None:
                continue

            if element_id_int(elem.Id) == element_id_int(wall.Id):
                continue

            if not isinstance(elem, Wall):
                continue

            if get_wall_orientation_group(elem) == "other":
                continue

            # Avoid tiny wall fragments / accidental joins.
            if get_wall_length(elem) < mm_to_internal(300):
                continue

            # Revit walls always have an exterior side concept,
            # but we still require a valid planar exterior face reference.
            face, face_ref = get_any_exterior_face_ref(elem)
            if face is None or face_ref is None:
                continue

            result[element_id_int(elem.Id)] = elem

    return list(result.values())


def expand_perimeter_walls_by_joins(perimeter_wall_ids, all_visible_walls):
    """
    Adds one level of joined walls to the perimeter set.
    This helps catch exterior recess/projection walls that sit inside the main perimeter
    but are physically joined to the facade chain.

    Intentionally NOT recursive:
    - safer
    - avoids pulling the whole interior partition network into facade dimensions
    """
    visible_by_id = {}
    for wall in all_visible_walls:
        visible_by_id[element_id_int(wall.Id)] = wall

    expanded = dict(visible_by_id)

    result_ids = set(perimeter_wall_ids)

    for wall_id in list(perimeter_wall_ids):
        wall = visible_by_id.get(wall_id)
        if wall is None:
            continue

        joined_walls = get_joined_walls_any_orientation(wall)

        for joined_wall in joined_walls:
            joined_id = element_id_int(joined_wall.Id)

            # Only add if it is actually visible in this plan view.
            if joined_id not in visible_by_id:
                continue

            result_ids.add(joined_id)

    return result_ids


def add_recess_refs_for_facade(document, facade_name, middle_items, end_data, ref_items, exterior_wall_ids):
    candidates = {}
    if facade_name in ["EAST", "WEST"]:
        for item in middle_items:
            for hwall in get_joined_horizontal_walls_from_vertical_wall(item["wall"]):
                if element_id_int(hwall.Id) in exterior_wall_ids: candidates[element_id_int(hwall.Id)] = hwall
        for wall_id, wall in candidates.items():
            if end_data["first_wall"] and element_id_int(wall.Id) == element_id_int(end_data["first_wall"].Id): continue
            if end_data["last_wall"] and element_id_int(wall.Id) == element_id_int(end_data["last_wall"].Id): continue
            face, face_ref = get_any_exterior_face_ref(wall)
            if face and face_ref: append_ref_item(document, ref_items, wall, face_ref, face.Origin.Y, "recess_wall_exterior_face")
    else:
        for item in middle_items:
            for vwall in get_joined_vertical_walls_from_horizontal_wall(item["wall"]):
                if element_id_int(vwall.Id) in exterior_wall_ids: candidates[element_id_int(vwall.Id)] = vwall
        for wall_id, wall in candidates.items():
            if end_data["first_wall"] and element_id_int(wall.Id) == element_id_int(end_data["first_wall"].Id): continue
            if end_data["last_wall"] and element_id_int(wall.Id) == element_id_int(end_data["last_wall"].Id): continue
            face, face_ref = get_any_exterior_face_ref(wall)
            if face and face_ref: append_ref_item(document, ref_items, wall, face_ref, face.Origin.X, "recess_wall_exterior_face")


def sort_horizontal_items(items): return sorted(items, key=lambda x: x["face"].Origin.Y)
def sort_vertical_items(items): return sorted(items, key=lambda x: x["face"].Origin.X)


def get_side_middle_walls(facade_name, horizontal_items, vertical_items):
    middle = []
    if facade_name == "EAST":
        for item in vertical_items:
            wall = item["wall"]
            face, face_ref = get_exterior_face_ref_for_side(wall, EAST_RAY)
            if face and face_ref and face.FaceNormal.X > 0.95: middle.append({"wall": wall, "face": face, "face_ref": face_ref})
    elif facade_name == "WEST":
        for item in vertical_items:
            wall = item["wall"]
            face, face_ref = get_exterior_face_ref_for_side(wall, WEST_RAY)
            if face and face_ref and face.FaceNormal.X < -0.95: middle.append({"wall": wall, "face": face, "face_ref": face_ref})
    elif facade_name == "NORTH":
        for item in horizontal_items:
            wall = item["wall"]
            face, face_ref = get_exterior_face_ref_for_side(wall, NORTH_RAY)
            if face and face_ref and face.FaceNormal.Y > 0.95: middle.append({"wall": wall, "face": face, "face_ref": face_ref})
    elif facade_name == "SOUTH":
        for item in horizontal_items:
            wall = item["wall"]
            face, face_ref = get_exterior_face_ref_for_side(wall, SOUTH_RAY)
            if face and face_ref and face.FaceNormal.Y < -0.95: middle.append({"wall": wall, "face": face, "face_ref": face_ref})
    return middle


def add_extreme_refs_for_facade(document, facade_name, horizontal_items, vertical_items, ref_items):
    data = {"first_wall": None, "last_wall": None}
    if facade_name in ["EAST", "WEST"]:
        if len(horizontal_items) == 0: return data
        first_wall = horizontal_items[0]["wall"]
        last_wall = horizontal_items[-1]["wall"]
        first_face, first_ref = get_exterior_face_ref_for_side(first_wall, SOUTH_RAY)
        last_face, last_ref = get_exterior_face_ref_for_side(last_wall, NORTH_RAY)
        data["first_wall"] = first_wall
        data["last_wall"] = last_wall
        if first_face and first_ref: append_ref_item(document, ref_items, first_wall, first_ref, first_face.Origin.Y, "first_end_face")
        if last_face and last_ref: append_ref_item(document, ref_items, last_wall, last_ref, last_face.Origin.Y, "last_end_face")
    else:
        if len(vertical_items) == 0: return data
        first_wall = vertical_items[0]["wall"]
        last_wall = vertical_items[-1]["wall"]
        first_face, first_ref = get_exterior_face_ref_for_side(first_wall, WEST_RAY)
        last_face, last_ref = get_exterior_face_ref_for_side(last_wall, EAST_RAY)
        data["first_wall"] = first_wall
        data["last_wall"] = last_wall
        if first_face and first_ref: append_ref_item(document, ref_items, first_wall, first_ref, first_face.Origin.X, "first_end_face")
        if last_face and last_ref: append_ref_item(document, ref_items, last_wall, last_ref, last_face.Origin.X, "last_end_face")
    return data


def get_dimension_line_for_facade(plan_view, facade_name, bounds, offset):
    view_z = get_plan_view_z(plan_view)

    if facade_name == "EAST":
        return Line.CreateBound(
            XYZ(bounds["east"] + offset, bounds["south"], view_z),
            XYZ(bounds["east"] + offset, bounds["north"], view_z)
        )

    if facade_name == "WEST":
        return Line.CreateBound(
            XYZ(bounds["west"] - offset, bounds["south"], view_z),
            XYZ(bounds["west"] - offset, bounds["north"], view_z)
        )

    if facade_name == "NORTH":
        return Line.CreateBound(
            XYZ(bounds["west"], bounds["north"] + offset, view_z),
            XYZ(bounds["east"], bounds["north"] + offset, view_z)
        )

    if facade_name == "SOUTH":
        return Line.CreateBound(
            XYZ(bounds["west"], bounds["south"] - offset, view_z),
            XYZ(bounds["east"], bounds["south"] - offset, view_z)
        )

    return None


def get_plan_visible_walls(document, plan_view):
    """
    Returns walls that are actually visible in the given plan view.
    This is intentionally view-based, so upper floor views do not accidentally
    reuse lower floor wall references from the 3D ray test.
    """
    try:
        return FilteredElementCollector(document, plan_view.Id) \
            .OfClass(Wall) \
            .WhereElementIsNotElementType() \
            .ToElements()
    except:
        return []


def collect_facade_wall_data(document, plan_view, view3d=None):
    """
    Facade wall collection based on the selected plan view.

    Previous versions used a 3D ray test. That can work on Level 1, but on upper
    floor plans it can accidentally pick lower-level walls or miss walls depending
    on 3D view visibility / section box / wall height / view range.

    This version uses only walls visible in the selected plan view, then keeps
    likely perimeter walls by exterior face position.
    """
    bbox = plan_view.CropBox
    if bbox is None:
        return [], [], set(), make_bounds_from_cropbox(bbox)

    visible_walls = get_plan_visible_walls(document, plan_view)

    candidates = []

    for wall in visible_walls:
        group = get_wall_orientation_group(wall)
        if group == "other":
            continue

        if group == "vertical":
            east_face, east_ref = get_exterior_face_ref_for_side(wall, EAST_RAY)
            west_face, west_ref = get_exterior_face_ref_for_side(wall, WEST_RAY)

            if east_face and east_ref:
                candidates.append({
                    "wall": wall,
                    "face": east_face,
                    "face_ref": east_ref,
                    "group": "vertical",
                    "side": "east",
                    "coord": east_face.Origin.X
                })

            if west_face and west_ref:
                candidates.append({
                    "wall": wall,
                    "face": west_face,
                    "face_ref": west_ref,
                    "group": "vertical",
                    "side": "west",
                    "coord": west_face.Origin.X
                })

        elif group == "horizontal":
            north_face, north_ref = get_exterior_face_ref_for_side(wall, NORTH_RAY)
            south_face, south_ref = get_exterior_face_ref_for_side(wall, SOUTH_RAY)

            if north_face and north_ref:
                candidates.append({
                    "wall": wall,
                    "face": north_face,
                    "face_ref": north_ref,
                    "group": "horizontal",
                    "side": "north",
                    "coord": north_face.Origin.Y
                })

            if south_face and south_ref:
                candidates.append({
                    "wall": wall,
                    "face": south_face,
                    "face_ref": south_ref,
                    "group": "horizontal",
                    "side": "south",
                    "coord": south_face.Origin.Y
                })

    if not candidates:
        return [], [], set(), make_bounds_from_cropbox(bbox)

    east_coords = [c["coord"] for c in candidates if c["side"] == "east"]
    west_coords = [c["coord"] for c in candidates if c["side"] == "west"]
    north_coords = [c["coord"] for c in candidates if c["side"] == "north"]
    south_coords = [c["coord"] for c in candidates if c["side"] == "south"]

    bounds = make_bounds_from_cropbox(bbox)

    if east_coords:
        bounds["east"] = max(east_coords)
    if west_coords:
        bounds["west"] = min(west_coords)
    if north_coords:
        bounds["north"] = max(north_coords)
    if south_coords:
        bounds["south"] = min(south_coords)

    # Keeps exterior recesses / projections close to the perimeter, but avoids
    # pulling deep interior partition walls into facade dimensioning.
    perimeter_tol = mm_to_internal(1800)

    perimeter_wall_ids = set()

    for c in candidates:
        wall = c["wall"]
        side = c["side"]
        coord = c["coord"]

        keep = False

        if side == "east":
            keep = coord >= bounds["east"] - perimeter_tol
        elif side == "west":
            keep = coord <= bounds["west"] + perimeter_tol
        elif side == "north":
            keep = coord >= bounds["north"] - perimeter_tol
        elif side == "south":
            keep = coord <= bounds["south"] + perimeter_tol

        if keep:
            perimeter_wall_ids.add(element_id_int(wall.Id))

    # Add one-step joined exterior walls to catch facade recess/projection returns.
    exterior_wall_ids = expand_perimeter_walls_by_joins(perimeter_wall_ids, visible_walls)

    horizontal_items_by_id = {}
    vertical_items_by_id = {}

    for c in candidates:
        wall = c["wall"]
        wall_id = element_id_int(wall.Id)

        if wall_id not in exterior_wall_ids:
            continue

        item = {
            "wall": wall,
            "face": c["face"],
            "face_ref": c["face_ref"]
        }

        if c["group"] == "horizontal":
            horizontal_items_by_id[wall_id] = item
        elif c["group"] == "vertical":
            vertical_items_by_id[wall_id] = item

    horizontal_walls = sort_horizontal_items(list(horizontal_items_by_id.values()))
    vertical_walls = sort_vertical_items(list(vertical_items_by_id.values()))

    return horizontal_walls, vertical_walls, exterior_wall_ids, bounds


def process_one_facade_dimension(document, plan_view, facade_name, bounds, horizontal_items, vertical_items, exterior_wall_ids, offset_mm, include_openings, include_recesses, dim_type):
    ref_items = []
    end_data = add_extreme_refs_for_facade(document, facade_name, horizontal_items, vertical_items, ref_items)
    if include_openings or include_recesses:
        middle_items = get_side_middle_walls(facade_name, horizontal_items, vertical_items)
        if include_openings: add_opening_refs_for_facade(document, plan_view, facade_name, middle_items, ref_items)
        if include_recesses: add_recess_refs_for_facade(document, facade_name, middle_items, end_data, ref_items, exterior_wall_ids)
    ref_array = build_reference_array_from_items(ref_items)
    if ref_array is None: return 0
    dim_line = get_dimension_line_for_facade(plan_view, facade_name, bounds, mm_to_internal(offset_mm))
    if dim_line is None: return 0
    return 1 if create_dimension_with_type(plan_view, dim_line, ref_array, dim_type) else 0


def process_facade_view(plan_view, facade_dim_offset_mm, facade_segments_offset_mm, facade_overall_offset_mm,
                        create_facade_dim, create_facade_segments_only, create_facade_overall,
                        include_facade_segments, dim_type, view3d):
    if plan_view.CropBox is None: return False, "facade: no crop box"
    horizontal_items, vertical_items, exterior_wall_ids, bounds = collect_facade_wall_data(doc, plan_view, view3d)
    if len(horizontal_items) == 0 and len(vertical_items) == 0:
        return False, "facade: no exterior wall candidates"
    created = 0
    for facade_name in ["EAST", "WEST", "NORTH", "SOUTH"]:
        # Detailed facade dimension: facade ends + openings + optional recess/projection segments
        if create_facade_dim:
            created += process_one_facade_dimension(
                doc, plan_view, facade_name, bounds, horizontal_items, vertical_items,
                exterior_wall_ids, facade_dim_offset_mm, True, include_facade_segments, dim_type
            )

        # Facade shape / segment dimension: facade ends + recess/projection segments, but ignores door/window openings
        if create_facade_segments_only:
            created += process_one_facade_dimension(
                doc, plan_view, facade_name, bounds, horizontal_items, vertical_items,
                exterior_wall_ids, facade_segments_offset_mm, False, True, dim_type
            )

        # Facade overall: only extreme end references
        if create_facade_overall:
            created += process_one_facade_dimension(
                doc, plan_view, facade_name, bounds, horizontal_items, vertical_items,
                exterior_wall_ids, facade_overall_offset_mm, False, False, dim_type
            )
    if created > 0: return True, "facade: {0} dimensions created".format(created)
    return False, "facade: no dimensions created"


# --------------------------------------------------
# UI
# --------------------------------------------------
class AutoDimUI(forms.WPFWindow):
    def __init__(self):
        forms.WPFWindow.__init__(self, "ui.xaml")
        self._plan_views_by_id = {}
        self._dim_types_by_id = {}
        self._last_report_lines = []
        self.populate_plan_views()
        self.populate_dim_types()

    def populate_plan_views(self):
        self.planViewList.Items.Clear()
        plan_views = get_all_candidate_plan_views(doc)
        active_view_id = element_id_int(doc.ActiveView.Id) if doc.ActiveView else None
        for pv in plan_views:
            cb = CheckBox()
            cb.Content = get_element_name(pv)
            cb.Tag = element_id_int(pv.Id)
            cb.Margin = Thickness(0, 2, 0, 2)
            if element_id_int(pv.Id) == active_view_id: cb.IsChecked = True
            self.planViewList.Items.Add(cb)
            self._plan_views_by_id[element_id_int(pv.Id)] = pv

    def populate_dim_types(self):
        self.dimTypeCombo.Items.Clear()
        dim_types = get_linear_dimension_types(doc)
        for dt in dim_types:
            item = ComboBoxItem()
            item.Content = get_element_name(dt)
            item.Tag = element_id_int(dt.Id)
            self.dimTypeCombo.Items.Add(item)
            self._dim_types_by_id[element_id_int(dt.Id)] = dt
        if self.dimTypeCombo.Items.Count > 0: self.dimTypeCombo.SelectedIndex = 0

    def get_selected_dim_type(self):
        selected_item = self.dimTypeCombo.SelectedItem
        if selected_item is None: return None
        try:
            return self._dim_types_by_id.get(int(selected_item.Tag))
        except:
            return None

    def get_selected_plan_views(self):
        selected = []
        for item in self.planViewList.Items:
            try:
                if item.IsChecked:
                    view_id_int = int(item.Tag)
                    if view_id_int in self._plan_views_by_id: selected.append(self._plan_views_by_id[view_id_int])
            except:
                pass
        return selected

    def selectAllCheckBox_Changed(self, sender, args):
        is_checked = bool(self.selectAllCheckBox.IsChecked)
        for item in self.planViewList.Items:
            try: item.IsChecked = is_checked
            except: pass

    def createUpdateButton_Click(self, sender, args):
        selected_views = self.get_selected_plan_views()
        if not selected_views:
            forms.alert("Please select at least one plan view.", title="No View Selected")
            return
        create_facade_dim = bool(self.chkFacadeDim.IsChecked)
        create_facade_segments_only = bool(self.chkFacadeSegmentsOnly.IsChecked)
        create_facade_overall = bool(self.chkFacadeOverall.IsChecked)
        create_grid_segment = bool(self.chkGridSegment.IsChecked)
        create_grid_overall = bool(self.chkGridOverall.IsChecked)
        include_facade_segments = bool(self.chkIncludeFacadeSegment.IsChecked)

        if not create_facade_dim and not create_facade_segments_only and not create_facade_overall and not create_grid_segment and not create_grid_overall:
            forms.alert("Please select at least one dimension option.", title="No Dimension Option")
            return

        try:
            facade_offset_mm = float(self.facadeOffsetText.Text)
            between_offset_mm = float(self.betweenOffsetText.Text)
        except:
            forms.alert("Offset values must be numeric.", title="Invalid Offset")
            return

        dim_type = self.get_selected_dim_type()
        if dim_type is None:
            forms.alert("Please select a dimension type.", title="No Dimension Type")
            return

        # User-controlled order from facade to outside.
        # Selected options are sorted by these order values; offsets are assigned in that order.
        option_rows = [
            ("facade_dim", create_facade_dim, self.orderFacadeDimText.Text),
            ("facade_segments", create_facade_segments_only, self.orderFacadeSegmentsText.Text),
            ("facade_overall", create_facade_overall, self.orderFacadeOverallText.Text),
            ("grid_segment", create_grid_segment, self.orderGridSegmentText.Text),
            ("grid_overall", create_grid_overall, self.orderGridOverallText.Text)
        ]

        selected_order_rows = []
        used_orders = set()
        try:
            for key, enabled, order_text in option_rows:
                if not enabled:
                    continue
                order_value = int(order_text)
                if order_value < 1:
                    raise Exception("Order must be 1 or greater.")
                if order_value in used_orders:
                    raise Exception("Two selected dimension options have the same order number: {0}".format(order_value))
                used_orders.add(order_value)
                selected_order_rows.append((order_value, key))
        except Exception as order_error:
            forms.alert("Please check dimension order values.\n{}".format(str(order_error)), title="Invalid Order")
            return

        selected_order_rows = sorted(selected_order_rows, key=lambda x: x[0])
        offset_map = {}
        for idx, row in enumerate(selected_order_rows):
            key = row[1]
            offset_map[key] = facade_offset_mm + (idx * between_offset_mm)

        facade_dim_offset = offset_map.get("facade_dim", facade_offset_mm)
        facade_segments_offset = offset_map.get("facade_segments", facade_offset_mm)
        facade_overall_offset = offset_map.get("facade_overall", facade_offset_mm)
        grid_segment_offset = offset_map.get("grid_segment", facade_offset_mm)
        grid_overall_offset = offset_map.get("grid_overall", facade_offset_mm)

        view3d = get_first_working_3d_view(doc)

        processed_views = []
        skipped_views = []
        t = Transaction(doc, "Auto Dim - Selected Plan Views")
        t.Start()
        try:
            for plan_view in selected_views:
                view_name = get_element_name(plan_view)
                view_messages = []
                view_ok = False
                try:
                    if create_facade_dim or create_facade_segments_only or create_facade_overall:
                        ok, message = process_facade_view(
                            plan_view,
                            facade_dim_offset,
                            facade_segments_offset,
                            facade_overall_offset,
                            create_facade_dim,
                            create_facade_segments_only,
                            create_facade_overall,
                            include_facade_segments,
                            dim_type,
                            view3d
                        )
                        view_messages.append(message)
                        if ok: view_ok = True
                    if create_grid_segment or create_grid_overall:
                        ok, message = process_grid_view(plan_view, grid_segment_offset, grid_overall_offset, create_grid_segment, create_grid_overall, dim_type, view3d)
                        view_messages.append(message)
                        if ok: view_ok = True
                    final_msg = "{0} - {1}".format(view_name, " | ".join(view_messages))
                    if view_ok: processed_views.append(final_msg)
                    else: skipped_views.append(final_msg)
                except Exception as inner_e:
                    skipped_views.append("{0} - error: {1}".format(view_name, str(inner_e)))
            t.Commit()
            report_lines = []
            report_lines.append("Auto dimensions finished.")
            report_lines.append("")
            report_lines.append("Dimension type: {0}".format(get_element_name(dim_type)))
            report_lines.append("")
            report_lines.append("Processed views: {0}".format(len(processed_views)))
            if processed_views: report_lines.extend(processed_views)
            report_lines.append("")
            report_lines.append("Skipped views: {0}".format(len(skipped_views)))
            if skipped_views: report_lines.extend(skipped_views)
            self._last_report_lines = report_lines
            forms.alert("\n".join(report_lines), title="Success")
        except Exception as e:
            if t.GetStatus() == TransactionStatus.Started: t.RollBack()
            forms.alert("Error:\n{0}".format(str(e)), title="Auto Dim Error")

    def reportButton_Click(self, sender, args):
        if self._last_report_lines:
            forms.alert("\n".join(self._last_report_lines), title="Last Report")
        else:
            forms.alert("No report yet. Run CREATE/UPDATE first.", title="Report")


ui = AutoDimUI()
ui.show_dialog()
