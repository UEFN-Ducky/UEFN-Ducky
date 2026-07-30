"""Data table registry tools: inspect, create, and (re)fill DataTable assets.

Composable primitives:

  READ    data_table_capabilities, list_data_tables, get_data_table_info,
          get_data_table_rows
  CREATE  create_data_table
  CHANGE  fill_data_table_from_json, fill_data_table_from_csv  (both REPLACE all rows)

Per-row writes are not exposed to editor Python (even in mainline UE); the
supported loop is read rows -> edit JSON/CSV -> fill. Every tool guards on
availability and self-describes what IS present instead of crashing, because
UEFN's embedded Python may expose only part of DataTableFunctionLibrary.
"""

from __future__ import annotations

from typing import List, Optional

import unreal

from listener.dispatch import register
from listener.project_paths import pin_project_folder

_DT_CLASSES = ("DataTable", "DataTableFunctionLibrary", "DataTableFactory")

_CELL_CAP = 400
_ROW_HARD_CAP = 100
_COLUMN_CAP = 40
_HARD_LIST_CAP = 200


def _capabilities() -> dict:
    lib = getattr(unreal, "DataTableFunctionLibrary", None)
    return {
        "classes": {name: hasattr(unreal, name) for name in _DT_CLASSES},
        "library_methods": sorted(n for n in dir(lib) if "data_table" in n and not n.startswith("_")) if lib else [],
    }


def _library():
    lib = getattr(unreal, "DataTableFunctionLibrary", None)
    if lib is None:
        raise ValueError(f"unreal.DataTableFunctionLibrary is not exposed in this UEFN build. Capabilities: {_capabilities()}")
    return lib


def _load_table(path: str):
    table = unreal.EditorAssetLibrary.load_asset(path)
    if table is None:
        raise ValueError(f"DataTable not found: {path}")
    return table


def _first_method(obj, names: List[str]):
    for n in names:
        fn = getattr(obj, n, None)
        if callable(fn):
            return n, fn
    return None, None


def _row_names(table) -> List[str]:
    _, fn = _first_method(_library(), ["get_data_table_row_names"])
    if fn is None:
        raise ValueError(f"Row-name API missing in this build. Library methods: {_capabilities()['library_methods']}")
    return [str(n) for n in fn(table)]


def _column_names(table) -> Optional[List[str]]:
    _, fn = _first_method(_library(), ["get_data_table_column_names", "get_data_table_column_name_list"])
    if fn is None:
        return None
    try:
        return [str(n) for n in fn(table)]
    except Exception:
        return None


from listener.registry.asset_registry import assets_by_class as _assets_by_class


def data_table_capabilities() -> dict:
    """Probe which DataTable classes/methods this UEFN build exposes (run first)."""
    caps = _capabilities()
    caps["notes"] = [
        "No per-row write API in Python: read rows -> edit JSON/CSV -> fill_data_table_from_* (replaces ALL rows).",
        "get_data_table_rows needs explicit columns when column discovery is unavailable.",
    ]
    return caps


def list_data_tables(search: str = "", offset: int = 0, limit: int = 50) -> dict:
    """List DataTable assets in the project (filter with ``search``, paged)."""
    limit = max(0, min(int(limit), _HARD_LIST_CAP))
    q = (search or "").strip().lower()
    paths: List[str] = []
    for data in _assets_by_class("/Script/Engine", "DataTable"):
        try:
            full = f"{data.package_name}.{data.asset_name}"
        except Exception:
            continue
        if q and q not in full.lower():
            continue
        paths.append(full)
    paths.sort()
    total = len(paths)
    page = paths[offset : offset + limit]
    return {"data_tables": page, "count": len(page), "total": total, "truncated": offset + len(page) < total}


def get_data_table_info(data_table_path: str) -> dict:
    """Read a data table's row struct, row names, and columns (when discoverable)."""
    table = _load_table(data_table_path)
    info: dict = {"data_table_path": data_table_path}
    try:
        struct = table.get_editor_property("row_struct")
        info["row_struct"] = str(struct.get_path_name()) if struct else None
    except Exception as e:
        info["row_struct_error"] = str(e)
    rows = _row_names(table)
    info["row_count"] = len(rows)
    info["row_names"] = rows[:_HARD_LIST_CAP]
    info["row_names_truncated"] = len(rows) > _HARD_LIST_CAP
    cols = _column_names(table)
    if cols is None:
        info["columns"] = None
        info["columns_note"] = (
            "Column discovery API missing in this build — pass columns explicitly to "
            "get_data_table_rows (the row struct's field names)."
        )
    else:
        info["columns"] = cols[:_COLUMN_CAP]
    return info


def get_data_table_rows(
    data_table_path: str,
    columns: Optional[List[str]] = None,
    row_names: Optional[List[str]] = None,
    offset: int = 0,
    limit: int = 30,
) -> dict:
    """Read rows as strings (paged). ``columns`` = row-struct field names; required if discovery is unavailable."""
    limit = max(0, min(int(limit), _ROW_HARD_CAP))
    table = _load_table(data_table_path)
    all_rows = _row_names(table)
    cols = list(columns or []) or _column_names(table)
    if not cols:
        raise ValueError(
            "No columns given and column discovery unavailable — pass columns=[...] "
            "(row struct field names; get_data_table_info shows the row struct)."
        )
    cols = cols[:_COLUMN_CAP]
    _, col_fn = _first_method(_library(), ["get_data_table_column_as_string"])
    if col_fn is None:
        raise ValueError(f"get_data_table_column_as_string missing in this build. Library methods: {_capabilities()['library_methods']}")
    col_values: dict = {}
    col_errors: dict = {}
    for c in cols:
        try:
            col_values[c] = [str(v)[:_CELL_CAP] for v in col_fn(table, unreal.Name(c))]
        except Exception as e:
            col_values[c] = None
            col_errors[c] = str(e)
    if row_names:
        indices = [all_rows.index(r) for r in row_names if r in all_rows]
    else:
        indices = list(range(offset, min(offset + limit, len(all_rows))))
    rows_out = []
    for i in indices:
        if i >= len(all_rows):
            continue
        values = {}
        for c in cols:
            vals = col_values.get(c)
            values[c] = vals[i] if vals is not None and i < len(vals) else None
        rows_out.append({"row": all_rows[i], "values": values})
    out = {
        "data_table_path": data_table_path,
        "columns": cols,
        "rows": rows_out,
        "count": len(rows_out),
        "total": len(all_rows),
        "truncated": not row_names and offset + len(rows_out) < len(all_rows),
    }
    if col_errors:
        out["column_errors"] = col_errors
    return out


def fill_data_table_from_json(data_table_path: str, json_string: str) -> dict:
    """REPLACE ALL rows of a data table from a JSON array (field names must match the row struct)."""
    table = _load_table(data_table_path)
    _, fn = _first_method(_library(), ["fill_data_table_from_json_string"])
    if fn is None:
        raise ValueError(f"fill_data_table_from_json_string missing in this build. Library methods: {_capabilities()['library_methods']}")
    if not bool(fn(table, json_string)):
        raise RuntimeError(
            "Engine rejected the JSON — field names must match the row struct exactly "
            "(see get_data_table_info); check the editor Output Log for the offending row/field."
        )
    unreal.EditorAssetLibrary.save_loaded_asset(table, only_if_is_dirty=False)
    return {"data_table_path": data_table_path, "row_count": len(_row_names(table))}


def fill_data_table_from_csv(data_table_path: str, csv_string: str) -> dict:
    """REPLACE ALL rows of a data table from a CSV string (header row must match the row struct)."""
    table = _load_table(data_table_path)
    _, fn = _first_method(_library(), ["fill_data_table_from_csv_string"])
    if fn is None:
        raise ValueError(f"fill_data_table_from_csv_string missing in this build. Library methods: {_capabilities()['library_methods']}")
    if not bool(fn(table, csv_string)):
        raise RuntimeError(
            "Engine rejected the CSV — the header row must match the row struct exactly "
            "(see get_data_table_info); check the editor Output Log for the offending row/field."
        )
    unreal.EditorAssetLibrary.save_loaded_asset(table, only_if_is_dirty=False)
    return {"data_table_path": data_table_path, "row_count": len(_row_names(table))}


def create_data_table(asset_name: str, row_struct: str, folder: str = "") -> dict:
    """Create a DataTable asset for ``row_struct`` (an unreal struct name or user-struct asset path)."""
    folder = pin_project_folder(folder, default_leaf="Data")
    factory_cls = getattr(unreal, "DataTableFactory", None)
    if factory_cls is None:
        raise ValueError(f"unreal.DataTableFactory is not exposed in this UEFN build. Capabilities: {_capabilities()}")
    table_cls = getattr(unreal, "DataTable", None)
    if table_cls is None:
        raise ValueError(f"unreal.DataTable is not exposed in this UEFN build. Capabilities: {_capabilities()}")
    struct_obj = None
    if "/" in row_struct:
        struct_obj = unreal.load_object(None, row_struct)
    else:
        holder = getattr(unreal, row_struct, None)
        static_struct = getattr(holder, "static_struct", None) if holder is not None else None
        if callable(static_struct):
            try:
                struct_obj = static_struct()
            except Exception:
                struct_obj = None
    if struct_obj is None:
        raise ValueError(
            f"Row struct not found: {row_struct!r}. Pass an unreal struct name "
            "(search_unreal_api to find one) or a full asset path to a user struct."
        )
    unreal.EditorAssetLibrary.make_directory(folder)
    full = f"{folder.rstrip('/')}/{asset_name}"
    if unreal.EditorAssetLibrary.does_asset_exist(full):
        raise ValueError(f"Asset already exists: {full} (delete_asset first to replace)")
    factory = factory_cls()
    factory.set_editor_property("struct", struct_obj)
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    table = asset_tools.create_asset(asset_name, folder, table_cls, factory)
    if table is None:
        raise RuntimeError(f"create_asset returned None for {full}")
    unreal.EditorAssetLibrary.save_loaded_asset(table, only_if_is_dirty=False)
    return {"data_table_path": str(table.get_path_name()), "row_struct": row_struct, "folder": folder}


register("data_table_capabilities")(data_table_capabilities)
register("list_data_tables")(list_data_tables)
register("get_data_table_info")(get_data_table_info)
register("get_data_table_rows")(get_data_table_rows)
register("fill_data_table_from_json")(fill_data_table_from_json)
register("fill_data_table_from_csv")(fill_data_table_from_csv)
register("create_data_table")(create_data_table)
