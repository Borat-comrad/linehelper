from __future__ import annotations
from linehelper.catalogs.indexing import rebuild_search_index
from linehelper.catalogs.khs_etl_parser import normalize_part_number
from linehelper.catalogs.search import CatalogSearch
from linehelper.catalogs.store import CatalogStore
from linehelper.catalogs.validation import validate_staging
import sqlite3

def _staging():
 return {"catalog":{"document_type":"spare_parts_catalog","title":"T","source_filename":"a.pdf","source_checksum":"abc","machine_number":"1","revision":"05","revision_date":"2020-01-01","language":"ru","page_count":2,"parser_version":"test"},"equipment":{"manufacturer":"KHS","site":"S","equipment_type":"Innofill","model":"M","machine_number":"1","aliases_json":[]},"assemblies":[{"assembly_code":"A1","assembly_name":"Assembly","parent_assembly_code":None,"drawing_page":1,"parts_list_page":2,"source_page_start":1,"source_page_end":2,"metadata_json":{}}],"parts":[{"part_number_original":"X 12-3","part_number_normalized":"X123","part_name":"Valve","manufacturer":"KHS","metadata_json":{}}],"bom_items":[{"assembly_code":"A1","part_number_original":"X 12-3","position":"10","quantity":"1.000","unit":"шт","description":"Valve","child_assembly_code":None,"source_page":2,"reference_page":42,"source_row_order":1,"metadata_json":{}}],"pages":[{"page_number":1,"page_type":"drawing","assembly_code":"A1","text_quality":"good","metadata_json":{}},{"page_number":2,"page_type":"parts_list","assembly_code":"A1","text_quality":"good","metadata_json":{}}],"parser_warnings":[],"parser_errors":[]}
def test_part_number_normalization(): assert normalize_part_number(" x 12-3 ")=="X123"
def test_schema_and_duplicate_checksum(tmp_path):
 s=CatalogStore(tmp_path/"x.db"); s.import_staging(_staging()); assert s.has_checksum("abc")
 try: s.import_staging(_staging())
 except ValueError: pass
 else: assert False
def test_persistence_exact_and_fts(tmp_path):
 s=CatalogStore(tmp_path/"x.db"); s.import_staging(_staging()); rebuild_search_index(s); search=CatalogSearch(s)
 assert search.find_part_by_number("x123")[0].assembly_code=="A1"
 assert search.find_part_by_number("x123")[0].reference_page==42
 assert search.search_parts("Valve")[0].part_number=="X 12-3"
 assert search.find_assembly_parts("A1")[0].position=="10"
 with sqlite3.connect(tmp_path/"x.db") as c:
  assert c.execute("select source_page,reference_page from bom_items").fetchone()==(2,42)
def test_malformed_bom_is_rejected():
 staging=_staging(); staging["bom_items"][0]["assembly_code"]="missing"; assert validate_staging(staging)["quality_gate"]=="fail"

def test_reference_page_is_extracted_or_none():
 parser=__import__('linehelper.catalogs.khs_etl_parser',fromlist=['KHSETLCatalogParser']).KHSETLCatalogParser()
 text="10\nX123\nPart\n1,000\nштук\nStück\n"
 assert parser._rows(text,[42])[0]["reference_page"]==42
 assert parser._rows(text,[None])[0]["reference_page"] is None

def test_existing_schema_gains_reference_page_idempotently(tmp_path):
 path=tmp_path/"old.db"
 with sqlite3.connect(path) as c:
  c.execute("CREATE TABLE bom_items (bom_item_id INTEGER PRIMARY KEY, assembly_id INTEGER, part_id INTEGER, position TEXT, quantity TEXT, unit TEXT, description TEXT, child_assembly_id INTEGER, source_page INTEGER, source_row_order INTEGER, metadata_json TEXT)")
 store=CatalogStore(path); store.ensure_schema(); store.ensure_schema()
 with sqlite3.connect(path) as c:
  assert "reference_page" in {row[1] for row in c.execute("PRAGMA table_info(bom_items)")}

def test_backfill_changes_only_reference_page(tmp_path):
 store=CatalogStore(tmp_path/"x.db"); staging=_staging(); store.import_staging(staging)
 staging["bom_items"][0]["reference_page"]=99
 assert store.backfill_reference_pages(staging)==1
 with sqlite3.connect(tmp_path/"x.db") as c:
  assert c.execute("select position,quantity,source_page,reference_page from bom_items").fetchone()==("10","1.000",2,99)
