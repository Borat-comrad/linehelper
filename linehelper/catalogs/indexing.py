"""Rebuildable FTS5 projection of the normalized catalog tables."""
from __future__ import annotations
from .store import CatalogStore

def rebuild_search_index(store: CatalogStore) -> None:
    store.ensure_schema()
    with store.connect() as con:
        con.execute("DROP TABLE IF EXISTS catalog_search_fts")
        con.execute("CREATE VIRTUAL TABLE catalog_search_fts USING fts5(part_number_original, part_number_normalized, part_name, assembly_code, assembly_name, equipment_model, machine_number, revision, hierarchy_path, bom_item_id UNINDEXED)")
        con.execute("INSERT INTO catalog_search_fts SELECT p.part_number_original,p.part_number_normalized,COALESCE(p.part_name,''),a.assembly_code,COALESCE(a.assembly_name,''),COALESCE(e.model,''),COALESCE(c.machine_number,''),COALESCE(c.revision,''),COALESCE(a.hierarchy_path,''),b.bom_item_id FROM bom_items b JOIN parts p ON p.part_id=b.part_id JOIN assemblies a ON a.assembly_id=b.assembly_id JOIN catalogs c ON c.catalog_id=a.catalog_id JOIN equipment e ON e.equipment_id=c.equipment_id")
