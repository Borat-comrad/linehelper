"""SQLite source of truth for structured catalogs (separate from MemoryStore)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS catalog_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS equipment (equipment_id INTEGER PRIMARY KEY, manufacturer TEXT NOT NULL, site TEXT NOT NULL, equipment_type TEXT, model TEXT, machine_number TEXT, aliases_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS catalogs (catalog_id INTEGER PRIMARY KEY, equipment_id INTEGER NOT NULL REFERENCES equipment, document_type TEXT NOT NULL, title TEXT, source_filename TEXT NOT NULL, source_checksum TEXT NOT NULL UNIQUE, machine_number TEXT, revision TEXT, revision_date TEXT, language TEXT, page_count INTEGER NOT NULL, is_current INTEGER NOT NULL DEFAULT 1, imported_at TEXT NOT NULL, parser_version TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS assemblies (assembly_id INTEGER PRIMARY KEY, catalog_id INTEGER NOT NULL REFERENCES catalogs ON DELETE CASCADE, parent_assembly_id INTEGER REFERENCES assemblies, assembly_code TEXT NOT NULL, assembly_name TEXT, hierarchy_path TEXT, drawing_page INTEGER, parts_list_page INTEGER, source_page_start INTEGER NOT NULL, source_page_end INTEGER NOT NULL, metadata_json TEXT NOT NULL, UNIQUE(catalog_id, assembly_code));
CREATE TABLE IF NOT EXISTS parts (part_id INTEGER PRIMARY KEY, part_number_original TEXT NOT NULL UNIQUE, part_number_normalized TEXT NOT NULL, part_name TEXT, manufacturer TEXT, metadata_json TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_parts_normalized ON parts(part_number_normalized);
CREATE TABLE IF NOT EXISTS bom_items (bom_item_id INTEGER PRIMARY KEY, assembly_id INTEGER NOT NULL REFERENCES assemblies ON DELETE CASCADE, part_id INTEGER NOT NULL REFERENCES parts, position TEXT NOT NULL, quantity TEXT, unit TEXT, description TEXT, child_assembly_id INTEGER REFERENCES assemblies, source_page INTEGER NOT NULL, reference_page INTEGER, source_row_order INTEGER NOT NULL, metadata_json TEXT NOT NULL, UNIQUE(assembly_id, position, part_id, source_page, source_row_order));
CREATE TABLE IF NOT EXISTS catalog_pages (page_id INTEGER PRIMARY KEY, catalog_id INTEGER NOT NULL REFERENCES catalogs ON DELETE CASCADE, page_number INTEGER NOT NULL, page_type TEXT NOT NULL, assembly_id INTEGER REFERENCES assemblies, text_quality TEXT NOT NULL, metadata_json TEXT NOT NULL, UNIQUE(catalog_id, page_number));
"""


class CatalogStore:
    def __init__(self, path: Path | str): self.path = Path(path)
    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.path); con.row_factory = sqlite3.Row; con.execute("PRAGMA foreign_keys = ON"); return con
    def ensure_schema(self) -> None:
        with self.connect() as con:
            con.executescript(_SCHEMA)
            columns = {row["name"] for row in con.execute("PRAGMA table_info(bom_items)")}
            if "reference_page" not in columns:
                con.execute("ALTER TABLE bom_items ADD COLUMN reference_page INTEGER")
            con.execute("INSERT OR REPLACE INTO catalog_metadata VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
    def has_checksum(self, checksum: str) -> bool:
        self.ensure_schema()
        with self.connect() as con: return con.execute("SELECT 1 FROM catalogs WHERE source_checksum=?", (checksum,)).fetchone() is not None
    def import_staging(self, staging: dict[str, Any]) -> int:
        self.ensure_schema()
        checksum = staging["catalog"]["source_checksum"]
        with self.connect() as con:
            if con.execute("SELECT 1 FROM catalogs WHERE source_checksum=?", (checksum,)).fetchone():
                raise ValueError("catalog with this source checksum is already imported")
            now = datetime.now(timezone.utc).isoformat()
            equip = staging["equipment"]
            cur = con.execute("INSERT INTO equipment(manufacturer,site,equipment_type,model,machine_number,aliases_json,created_at) VALUES(?,?,?,?,?,?,?)", (equip["manufacturer"],equip["site"],equip["equipment_type"],equip["model"],equip["machine_number"],json.dumps(equip["aliases_json"]),now))
            equipment_id = cur.lastrowid; cat = staging["catalog"]
            cur = con.execute("INSERT INTO catalogs(equipment_id,document_type,title,source_filename,source_checksum,machine_number,revision,revision_date,language,page_count,imported_at,parser_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (equipment_id,cat["document_type"],cat["title"],cat["source_filename"],checksum,cat["machine_number"],cat["revision"],cat["revision_date"],cat["language"],cat["page_count"],now,cat["parser_version"]))
            catalog_id = cur.lastrowid; ids = {}
            for a in staging["assemblies"]:
                cur = con.execute("INSERT INTO assemblies(catalog_id,parent_assembly_id,assembly_code,assembly_name,hierarchy_path,drawing_page,parts_list_page,source_page_start,source_page_end,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)", (catalog_id,None,a["assembly_code"],a["assembly_name"],None,a["drawing_page"],a["parts_list_page"],a["source_page_start"],a["source_page_end"],json.dumps(a["metadata_json"])))
                ids[a["assembly_code"]] = cur.lastrowid
            for a in staging["assemblies"]:
                parent = a["parent_assembly_code"]
                path = a["assembly_code"] if not parent else f"{parent}/{a['assembly_code']}"
                con.execute("UPDATE assemblies SET parent_assembly_id=?, hierarchy_path=? WHERE assembly_id=?", (ids.get(parent),path,ids[a["assembly_code"]]))
            part_ids = {}
            for p in staging["parts"]:
                row = con.execute("SELECT part_id FROM parts WHERE part_number_original=?", (p["part_number_original"],)).fetchone()
                if row: part_ids[p["part_number_original"]] = row[0]; continue
                cur=con.execute("INSERT INTO parts(part_number_original,part_number_normalized,part_name,manufacturer,metadata_json) VALUES(?,?,?,?,?)",(p["part_number_original"],p["part_number_normalized"],p["part_name"],p["manufacturer"],json.dumps(p["metadata_json"])))
                part_ids[p["part_number_original"]]=cur.lastrowid
            for b in staging["bom_items"]:
                con.execute("INSERT INTO bom_items(assembly_id,part_id,position,quantity,unit,description,child_assembly_id,source_page,reference_page,source_row_order,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (ids[b["assembly_code"]],part_ids[b["part_number_original"]],b["position"],b["quantity"],b["unit"],b["description"],ids.get(b["child_assembly_code"]),b["source_page"],b.get("reference_page"),b["source_row_order"],json.dumps(b["metadata_json"])))
            for p in staging["pages"]:
                con.execute("INSERT INTO catalog_pages(catalog_id,page_number,page_type,assembly_id,text_quality,metadata_json) VALUES(?,?,?,?,?,?)",(catalog_id,p["page_number"],p["page_type"],ids.get(p["assembly_code"]),p["text_quality"],json.dumps(p["metadata_json"])))
        return int(catalog_id)

    def backfill_reference_pages(self, staging: dict[str, Any]) -> int:
        """Update only `reference_page` for an already imported checksum."""
        self.ensure_schema()
        with self.connect() as con:
            catalog = con.execute("SELECT catalog_id FROM catalogs WHERE source_checksum=?", (staging["catalog"]["source_checksum"],)).fetchone()
            if catalog is None:
                raise ValueError("catalog checksum is not imported")
            updated = 0
            for row in staging["bom_items"]:
                cur = con.execute("UPDATE bom_items SET reference_page=? WHERE bom_item_id IN (SELECT b.bom_item_id FROM bom_items b JOIN assemblies a ON a.assembly_id=b.assembly_id JOIN parts p ON p.part_id=b.part_id WHERE a.catalog_id=? AND a.assembly_code=? AND b.position=? AND p.part_number_original=? AND b.source_page=? AND b.source_row_order=?)", (row.get("reference_page"), catalog["catalog_id"], row["assembly_code"], row["position"], row["part_number_original"], row["source_page"], row["source_row_order"]))
                updated += cur.rowcount
            con.execute("UPDATE catalogs SET parser_version=? WHERE catalog_id=?", (staging["catalog"]["parser_version"], catalog["catalog_id"]))
        return updated
